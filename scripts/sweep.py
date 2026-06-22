"""
sweep.py - orchestrate one dashboard refresh.

A refresh has two halves:

  (A) HARVEST + ENRICH  - Claude-driven, done before this script runs (see
      RUNBOOK.md). Claude uses Claude-in-Chrome to harvest Domain across the
      target-area suburb clusters <= $2.2M, extracts the per-listing fields,
      geocodes each address, auto-classifies outlook from description + cover
      image, and (for warehouse-character stock) fetches NSW zoning. The output
      is a "harvest file": {"generated_at_sydney": "...", "listings": [ ... ]}.

  (B) SCORE + DIFF + WRITE  - THIS script (pure computation, no network):
        1. compute catchments + Tier 1 + Tier 2 for every listing (score.py)
        2. diff against the most recent snapshot -> change_flag per listing,
           carry first_seen / days_on_market, detect WITHDRAWN / SOLD
        3. carry forward Adam's annotations from notes.json (by URL)
        4. write data/listings.json + a timestamped snapshot
        5. regenerate 07-property-shortlist.md (render.py)

CLI:
    python sweep.py <harvest_file.json>
        [--osm  data/osm_amenities.geojson]
        [--out  data/listings.json]
        [--no-render]            # skip regenerating 07
Run from anywhere; paths default relative to the dashboard folder.
"""

from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "..")
DATA = os.path.join(DASH, "data")

sys.path.insert(0, HERE)
import score as score_mod      # noqa: E402
import render as render_mod    # noqa: E402

SYD_TZ = dt.timezone(dt.timedelta(hours=10))   # AEST; AEDT (+11) Oct-Apr


def now_sydney():
    return dt.datetime.now(dt.timezone.utc).astimezone(SYD_TZ)


def listing_key(lst):
    return lst.get("url") or f"{lst.get('address','')}|{lst.get('suburb','')}"


def load_latest_snapshot(snap_dir):
    """Return the newest parseable snapshot dict, or None. Skips files that fail
    to parse or carry no 'listings' key (e.g. neutralised test snapshots)."""
    if not os.path.isdir(snap_dir):
        return None
    snaps = sorted((f for f in os.listdir(snap_dir) if f.endswith(".json")),
                   reverse=True)
    for name in snaps:
        path = os.path.join(snap_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.loads(fh.read())
        except (ValueError, OSError):
            continue
        if isinstance(data, dict) and "listings" in data:
            return data
    return None


def diff_and_flag(new_listings, prior_data, today):
    """Assign change_flag, carry first_seen/days_on_market, return carried-over
    WITHDRAWN/SOLD records for listings that vanished from the field."""
    prior = {}
    if prior_data:
        for l in prior_data.get("listings", []):
            prior[listing_key(l)] = l
    seen_keys = set()

    for l in new_listings:
        k = listing_key(l)
        seen_keys.add(k)
        old = prior.get(k)
        l.setdefault("first_seen", today)
        l["last_seen"] = today
        if old is None:
            l["change_flag"] = "NEW"
            continue
        l["first_seen"] = old.get("first_seen", today)
        try:
            fs = dt.date.fromisoformat(l["first_seen"])
            l["days_on_market"] = (dt.date.fromisoformat(today) - fs).days
        except Exception:
            l["days_on_market"] = None
        flag = "UNCHANGED"
        if (l.get("price_min") != old.get("price_min")
                or l.get("price_max") != old.get("price_max")):
            flag = "PRICE_CHANGED"
            l["prior_price_text"] = old.get("price_guide_text")
        new_oh = set(l.get("open_homes", []))
        old_oh = set(old.get("open_homes", []))
        if new_oh - old_oh and flag == "UNCHANGED":
            flag = "OPEN_HOME_ADDED"
        l["change_flag"] = flag

    carried = []
    for k, old in prior.items():
        if k in seen_keys:
            continue
        if old.get("change_flag") in ("WITHDRAWN", "SOLD"):
            old["last_seen"] = old.get("last_seen", today)
            carried.append(old)
            continue
        old["change_flag"] = old.get("departed_as") or "WITHDRAWN"
        old["departed_on"] = today
        carried.append(old)
    return new_listings, carried


def merge_incremental(new_scored, prior_listings, today):
    """Merge mode for email-alert ingestion (route A). Alert emails list only NEW
    matches, not the full current field, so we MUST NOT infer withdrawals from
    absence. We union the new listings onto the existing watchlist: add NEW ones,
    update price/open-home changes on existing ones, and leave everything else
    untouched (preserving status/notes). WITHDRAWN/SOLD are not auto-detected in
    this mode - they come from a later staleness check or Adam's manual marking."""
    merged = {listing_key(l): l for l in prior_listings}
    for l in new_scored:
        k = listing_key(l)
        old = merged.get(k)
        l.setdefault("first_seen", today)
        l["last_seen"] = today
        if old is None:
            l["change_flag"] = "NEW"
        else:
            l["first_seen"] = old.get("first_seen", today)
            flag = "UNCHANGED"
            if (l.get("price_min") != old.get("price_min")
                    or l.get("price_max") != old.get("price_max")):
                flag = "PRICE_CHANGED"
                l["prior_price_text"] = old.get("price_guide_text")
            elif set(l.get("open_homes", [])) - set(old.get("open_homes", [])):
                flag = "OPEN_HOME_ADDED"
            l["change_flag"] = flag
            if old.get("status"):
                l["status"] = old["status"]
            if old.get("note"):
                l["note"] = old["note"]
            try:
                fs = dt.date.fromisoformat(l["first_seen"])
                l["days_on_market"] = (dt.date.fromisoformat(today) - fs).days
            except Exception:
                l["days_on_market"] = None
        merged[k] = l
    return list(merged.values())


def carry_notes(listings, notes_path):
    if not os.path.exists(notes_path):
        return
    try:
        with open(notes_path, "r", encoding="utf-8") as fh:
            notes = json.loads(fh.read())
    except (ValueError, OSError):
        return
    for l in listings:
        n = notes.get(listing_key(l))
        if n:
            l["status"] = n.get("status")
            l["note"] = n.get("note")
            # Adam's manual accessibility verdict (authoritative). Shape:
            # {"step_free": true/false/null, "lift": true/false/null}. Applied
            # onto the listing so score.py's Tier 1 accessibility consumes it.
            # Callers must carry_notes BEFORE scoring for this to take effect.
            acc = n.get("accessibility")
            if isinstance(acc, dict) and (acc.get("step_free") is not None
                                          or acc.get("lift") is not None):
                l["accessibility"] = acc


def build_counts(active):
    return {
        "total": len(active),
        "tier1_pass": sum(1 for l in active if l.get("tier1", {}).get("pass")),
        "new": sum(1 for l in active if l.get("change_flag") == "NEW"),
        "price_changed": sum(1 for l in active if l.get("change_flag") == "PRICE_CHANGED"),
        "sold": sum(1 for l in active if l.get("change_flag") == "SOLD"),
        "withdrawn": sum(1 for l in active if l.get("change_flag") == "WITHDRAWN"),
    }


def main(argv):
    ap = argparse.ArgumentParser(description="Score + diff + write a dashboard sweep.")
    ap.add_argument("harvest", help="harvest JSON from the Claude-in-Chrome Domain sweep")
    ap.add_argument("--osm", default=os.path.join(DATA, "osm_amenities.geojson"))
    ap.add_argument("--out", default=os.path.join(DATA, "listings.json"))
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--incremental", action="store_true",
                    help="merge into the existing listings.json instead of replacing "
                         "(use for email-alert ingestion - alerts are new-only).")
    args = ap.parse_args(argv[1:])

    with open(args.harvest, "r", encoding="utf-8") as fh:
        harvest = json.loads(fh.read())
    listings = harvest["listings"] if isinstance(harvest, dict) else harvest

    # (1) score
    if os.path.exists(args.osm):
        amenities = score_mod.load_amenities(args.osm)
    else:
        print(f"WARNING: {args.osm} missing - catchments will be unknown.", file=sys.stderr)
        amenities = {c: [] for c in score_mod.CATCHMENT_CLASSES}
    for l in listings:
        score_mod.score_listing(l, amenities)

    # (2) diff / merge
    syd = now_sydney()
    today = syd.date().isoformat()
    snap_dir = os.path.join(DATA, "snapshots")
    if args.incremental:
        # Union onto the existing listings.json; never withdraw on absence.
        prior_listings = []
        if os.path.exists(args.out):
            try:
                with open(args.out, "r", encoding="utf-8") as fh:
                    prior_listings = json.loads(fh.read()).get("listings", [])
            except (ValueError, OSError):
                prior_listings = []
        active = merge_incremental(listings, prior_listings, today)
        carried = []
    else:
        # Full-snapshot mode: a complete sweep of the field; absence => departed.
        prior = load_latest_snapshot(snap_dir)
        active, carried = diff_and_flag(listings, prior, today)

    # (3) notes
    carry_notes(active + carried, os.path.join(DATA, "notes.json"))

    # (4) assemble + write
    all_listings = active + [c for c in carried if c.get("change_flag") in ("WITHDRAWN", "SOLD")]
    out = {
        "schema_version": 1,
        "generated_at": syd.astimezone(dt.timezone.utc).isoformat(),
        "generated_at_sydney": syd.strftime("%Y-%m-%d %H:%M %Z (Sydney)"),
        "sweep_provenance": harvest.get("sweep_provenance",
                                        "Live Domain sweep via Claude-in-Chrome."),
        "budget_ceiling": score_mod.BUDGET_CEILING,
        "target_area": ("Inner West: Zetland through Dulwich Hill, plus Drummoyne "
                        "north of Victoria Road (decision #15). Manly/Northern "
                        "Beaches excluded (decision #6)."),
        "counts": build_counts(active),
        "listings": all_listings,
    }
    os.makedirs(snap_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    snap_name = syd.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M") + "Z.json"
    with open(os.path.join(snap_dir, snap_name), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)

    print("Wrote " + args.out + " (" + str(out['counts']['total']) + " active, "
          + str(out['counts']['tier1_pass']) + " Tier-1 pass) and snapshot " + snap_name)

    # (5) regenerate 07
    if not args.no_render:
        md = render_mod.render(out)
        out_07 = os.path.join(DASH, "..", "07-property-shortlist.md")
        with open(out_07, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("Regenerated " + os.path.normpath(out_07))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
# (accessibility override applied via carry_notes before scoring; see RUNBOOK)
