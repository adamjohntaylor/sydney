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

  (C) VERIFY  - the status-verification leg (added 29 Sep 2026): every sweep
      also re-reads the pages of the active listings most likely to have left
      the market and marks them SOLD / UNDER_OFFER / WITHDRAWN on the page's
      own evidence. `--worklist` emits the pages to check; Claude reads them
      and writes data/status-checks/status-YYYYMMDD.json; the next sweep.py
      run (or dashboard Refresh) applies it. See build_status_worklist /
      apply_status_checks.

CLI:
    python sweep.py <harvest_file.json> [--incremental]
        [--osm  data/osm_amenities.geojson]
        [--out  data/listings.json]
        [--no-render]            # skip regenerating 07
        [--status-file PATH]     # apply page re-read results (repeatable)
    python sweep.py --worklist [N] [--worklist-all]   # emit verification worklist
    python sweep.py --status-file data/status-checks/status-20260929.json
        (status-only run: implies --incremental; also auto-applies any pending
         file in data/status-checks/ - so a bare `python sweep.py` after
         dropping a file there is enough)
Run from anywhere; paths default relative to the dashboard folder.
"""

from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "..")
DATA = os.path.join(DASH, "data")

sys.path.insert(0, HERE)
import score as score_mod      # noqa: E402
import render as render_mod    # noqa: E402

SYD_TZ = dt.timezone(dt.timedelta(hours=10))   # AEST; AEDT (+11) Oct-Apr

# Flags that mean "off the market" - such a listing lives in the dashboard's
# Withdrawn/sold tab and is excluded from the active counts. UNDER_OFFER sits
# here too: an under-offer property is not actionable, though it can return
# (deals fall through), so the merge below lets a genuinely re-listed property
# be revived by a bookmarklet click or a manual re-mark - never by a stale
# alert email re-read.
GONE_FLAGS = ("SOLD", "UNDER_OFFER", "WITHDRAWN")
# Transient "what changed" flags. Unlike the GONE_FLAGS (a market state), these
# describe an event, so they must expire. A flag set at refresh N is shown through
# refresh N+1 (it "lasts a second sweep") and reverts to UNCHANGED at N+2.
# Counter lives on the record as `flag_sweeps` (refreshes survived since set).
TRANSIENT_FLAGS = ("NEW", "PRICE_CHANGED", "OPEN_HOME_ADDED")
FLAG_SWEEP_LIFETIME = 2

# Map a harvest/bookmarklet 'listing_status' value onto a change_flag.
STATUS_TO_FLAG = {"sold": "SOLD", "under_offer": "UNDER_OFFER", "withdrawn": "WITHDRAWN"}


def now_sydney():
    return dt.datetime.now(dt.timezone.utc).astimezone(SYD_TZ)


def apply_listing_status(l, today):
    """If a harvested record carries listing_status (set by the bookmarklet's
    banner read, or by Claude enrichment during a sweep), convert it to the
    matching change_flag. 'on_market' clears a previous departure flag (the
    page itself is the freshest evidence the property is live again).
    Returns True if the record's flag was changed by the status."""
    status = (l.get("listing_status") or "").strip().lower()
    if not status:
        return False
    flag = STATUS_TO_FLAG.get(status)
    if flag:
        if l.get("change_flag") != flag:
            l["change_flag"] = flag
            l["departed_on"] = l.get("departed_on") or today
            l.setdefault("status_source", "sweep")  # caller may overwrite (e.g. bookmarklet)
            return True
        return False
    if status == "on_market" and l.get("change_flag") in GONE_FLAGS:
        l["change_flag"] = "UNCHANGED"
        l.pop("departed_on", None)
        l["relisted_on"] = today
        return True
    return False


def listing_key(lst):
    return lst.get("url") or f"{lst.get('address','')}|{lst.get('suburb','')}"


# ---------------------------------------------------------------------------
# Status verification leg (added 29 Sep 2026)
#
# Alert emails are new-only, so the incremental merge can never learn that a
# tracked listing has SOLD or been WITHDRAWN. Until now that knowledge arrived
# only opportunistically (a bookmarklet click, a sold-alert email, a manual
# mark), so stale stock accumulated on the active list. Every sweep now carries
# a deterministic VERIFICATION step:
#
#   1. build_status_worklist()  - the active listings most in need of a page
#      re-read, in priority order (auction passed > every open home passed >
#      longest since last sighting), capped per sweep so the backlog clears
#      over a few sweeps without re-hammering the portals.
#   2. Claude (via Claude-in-Chrome) opens each worklist URL - the individual
#      listing page, never a search scrape (decision #27) - and records what
#      the page itself says: a "status check" record.
#   3. apply_status_checks()    - converts those records into change flags in
#      listings.json, with provenance (status_source="sweep_check",
#      status_basis, status_checked_on).
#
# "Evidently withdrawn" is defined by PAGE EVIDENCE, never by absence from an
# alert: the page 404s / says "no longer available" / redirects to a search or
# suburb page / the listing id is gone. Those all arrive as one of the
# WITHDRAWN_EVIDENCE statuses below.
# ---------------------------------------------------------------------------

STATUS_SOURCE_CHECK = "sweep_check"

# Page states that mean the listing is evidently off the market with no sale
# recorded. All map to WITHDRAWN (with the specific evidence kept in basis).
WITHDRAWN_EVIDENCE = ("withdrawn", "not_found", "removed", "redirected", "no_longer_available",
                      "expired", "404")
# Page states that carry no usable evidence: leave the record alone, count the
# failure so a listing that never resolves is surfaced rather than silently
# re-queued forever.
INCONCLUSIVE = ("unknown", "error", "blocked", "captcha", "timeout", "")

DEFAULT_WORKLIST_CAP = 40      # pages per sweep
DEFAULT_RECHECK_DAYS = 7       # don't re-read a page verified this recently
MAX_INCONCLUSIVE = 3           # after this many failed reads, flag needs_manual_check


def _listing_id(url):
    """Numeric listing id at the end of a Domain/REA URL (stable across the
    /sold/ URL move) - same join key gmail_fetch uses for departures."""
    import re
    m = re.search(r"(?:-|/)(\d{6,12})/?(?:\?.*)?$", url or "")
    return m.group(1) if m else None


def _norm_addr(address, suburb):
    return f"{(address or '').lower().strip()}|{(suburb or '').lower().strip()}"


def _parse_date(s):
    """ISO date or datetime string -> date, else None."""
    if not s:
        return None
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _days_since(date_str, today):
    d = _parse_date(date_str)
    t = _parse_date(today)
    if d is None or t is None:
        return None
    return (t - d).days


def build_status_worklist(listings, today, cap=DEFAULT_WORKLIST_CAP,
                          recheck_days=DEFAULT_RECHECK_DAYS, include_all=False):
    """Return [{key, url, address, suburb, reason, priority, last_seen}, ...]
    for the active listings whose market status most needs re-verifying.

    Priority (lower = check first):
      0  auction date has passed (the property was either sold at/after auction
         or passed in - either way the page will now say)
      1  every recorded open home is in the past and none newer has arrived
      2  no sighting (alert / page read / status check) for > recheck_days
      3  everything else (only when include_all=True)
    Within a band, the longest-unsighted first. A listing verified within
    recheck_days is skipped so successive sweeps walk through the backlog
    instead of re-reading the same pages."""
    work = []
    for l in listings:
        if l.get("change_flag") in GONE_FLAGS:
            continue
        if not l.get("url"):
            continue
        checked = _days_since(l.get("status_checked_on"), today)
        if checked is not None and checked < recheck_days:
            continue
        seen_days = _days_since(l.get("last_seen"), today)
        if seen_days is None:
            seen_days = 10 ** 6   # never sighted -> most stale
        priority, reason = None, None
        auc = _days_since(l.get("auction"), today)
        if auc is not None and auc > 0:
            priority, reason = 0, f"auction {l.get('auction')[:10]} has passed"
        else:
            ohs = [d for d in (_parse_date(x) for x in l.get("open_homes", [])) if d]
            tday = _parse_date(today)
            if ohs and tday and max(ohs) < tday:
                priority, reason = 1, f"last open home {max(ohs).isoformat()} has passed"
            elif seen_days > recheck_days:
                priority, reason = 2, f"not sighted for {seen_days} days"
            elif include_all:
                priority, reason = 3, "routine re-verification"
        if priority is None:
            continue
        work.append({
            "key": listing_key(l),
            "url": l["url"],
            "listing_id": _listing_id(l["url"]),
            "address": l.get("address"),
            "suburb": l.get("suburb"),
            "last_seen": l.get("last_seen"),
            "first_seen": l.get("first_seen"),
            "priority": priority,
            "reason": reason,
            "inconclusive_checks": int(l.get("status_check_failures") or 0),
        })
    work.sort(key=lambda w: (w["priority"], -(_days_since(w["last_seen"], today) or 10 ** 6)))
    return work[:cap] if cap else work


def normalise_check_status(raw):
    """Map whatever the page reader wrote into the canonical vocabulary:
    sold | under_offer | withdrawn | on_market | inconclusive."""
    s = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if s in ("sold", "sold_prior_to_auction", "sold_at_auction"):
        return "sold"
    if s in ("under_offer", "under_contract", "deposit_taken", "contract_exchanged"):
        return "under_offer"
    if s in WITHDRAWN_EVIDENCE:
        return "withdrawn"
    if s in ("on_market", "for_sale", "active", "live", "listed"):
        return "on_market"
    return "inconclusive"


def apply_status_checks(checks, listings, today, source=STATUS_SOURCE_CHECK):
    """Apply status-check records to the watchlist IN PLACE.

    Each check: {"url": ..., "listing_status": ..., "status_basis": "...",
                 "final_url": ... (optional, after redirects),
                 "price_guide_text": ... (optional, refresh if present)}
    Matching: numeric listing id -> exact URL -> address+suburb. A check can
    only change a TRACKED listing, never inject one.

    Rules (same state machine as the bookmarklet / email legs):
      sold        -> SOLD (terminal; upgrades UNDER_OFFER)
      under_offer -> UNDER_OFFER, unless already SOLD
      withdrawn   -> WITHDRAWN (any of the page-evidence states), unless SOLD
      on_market   -> sighting: last_seen = today; revives a departed record
                     (relisted_on) - the live page is the freshest evidence
      inconclusive-> no flag change; status_check_failures += 1; at
                     MAX_INCONCLUSIVE the record gets needs_manual_check=True
    Every applied check stamps status_checked_on / status_source / status_basis.
    Returns (changed_count, details[list of str])."""
    by_id, by_url, by_addr = {}, {}, {}
    for l in listings:
        lid = _listing_id(l.get("url"))
        if lid:
            by_id.setdefault(lid, l)
        if l.get("url"):
            by_url.setdefault(l["url"], l)
        if l.get("address"):
            by_addr.setdefault(_norm_addr(l.get("address"), l.get("suburb")), l)

    changed, details = 0, []
    for chk in checks or []:
        url = chk.get("url") or chk.get("key") or ""
        target = None
        lid = _listing_id(url) or _listing_id(chk.get("final_url"))
        if lid and lid in by_id:
            target = by_id[lid]
        elif url in by_url:
            target = by_url[url]
        elif chk.get("address"):
            target = by_addr.get(_norm_addr(chk.get("address"), chk.get("suburb")))
        if target is None:
            details.append(f"unmatched: {url}")
            continue

        status = normalise_check_status(chk.get("listing_status"))
        basis = (chk.get("status_basis") or chk.get("listing_status") or "").strip()
        before = target.get("change_flag")

        if status == "inconclusive":
            raw = (chk.get("listing_status") or "").strip().lower()
            if raw in ("blocked", "error", "timeout", "captcha"):
                # The reader was refused, not the page: leave the record
                # untouched so the next pass (or another reader) retries it.
                details.append(f"not checked ({raw}): {url} {basis}".rstrip())
                continue
            n = int(target.get("status_check_failures") or 0) + 1
            target["status_check_failures"] = n
            target["status_checked_on"] = today
            if n >= MAX_INCONCLUSIVE:
                target["needs_manual_check"] = True
            details.append(f"inconclusive ({n}): {url} {basis}".rstrip())
            continue

        target.pop("status_check_failures", None)
        target.pop("needs_manual_check", None)
        target["status_checked_on"] = today

        if status == "on_market":
            target["last_seen"] = today
            if chk.get("price_guide_text"):
                target["price_guide_text"] = chk["price_guide_text"]
            if chk.get("resolved_url"):
                target["resolved_url"] = chk["resolved_url"]   # direct listing page (url kept: notes key)
            if before in GONE_FLAGS:
                target["change_flag"] = "UNCHANGED"
                target.pop("departed_on", None)
                target["relisted_on"] = today
                target["status_source"] = source
                target["status_basis"] = basis or "page read: on market"
                changed += 1
                details.append(f"revived {before}->UNCHANGED: {url}")
            continue

        flag = STATUS_TO_FLAG[status]                # sold/under_offer/withdrawn
        if before == "SOLD" and flag != "SOLD":
            details.append(f"kept SOLD (page said {status}): {url}")
            continue
        if before == flag:
            continue
        target["change_flag"] = flag
        target["departed_on"] = target.get("departed_on") or today
        target["status_source"] = source
        target["status_basis"] = basis or f"page read: {status}"
        if chk.get("final_url") and chk["final_url"] != target.get("url"):
            target["sold_url"] = chk["final_url"]
        if chk.get("resolved_url"):
            target["resolved_url"] = chk["resolved_url"]
        if chk.get("sold_date"):
            target["sold_date"] = chk["sold_date"]
        changed += 1
        details.append(f"{before}->{flag}: {url}")
    return changed, details


def is_status_stub(rec):
    """A harvest record that carries a listing_status but no listing substance
    (no address, beds or price). Such a record must UPDATE the tracked listing's
    status, never replace it - see merge_incremental."""
    if not (rec.get("listing_status") or "").strip():
        return False
    return not ((rec.get("address") or "").strip() or rec.get("beds")
                or rec.get("price_min") or rec.get("price_max")
                or any(ch.isdigit() for ch in (rec.get("price_guide_text") or "")))


def load_status_checks(path):
    """Read a status-check file: either a bare list of check records or
    {"checks": [...]} (optionally with generated_at_sydney / provenance)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.loads(fh.read())
    if isinstance(data, dict):
        return data.get("checks") or data.get("listings") or []
    return data


def apply_pending_status_files(listings, checks_dir, today):
    """Apply every *.json in data/status-checks/ that has not been applied yet
    (a sibling '<name>.applied' marker is written afterwards). Lets a Claude
    sweep drop its page-read results into the folder and have the next
    sweep.py run or dashboard Refresh pick them up. Returns (changed, details)."""
    total, details = 0, []
    if not os.path.isdir(checks_dir):
        return 0, details
    for name in sorted(os.listdir(checks_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(checks_dir, name)
        marker = path + ".applied"
        if os.path.exists(marker):
            continue
        try:
            checks = load_status_checks(path)
        except (ValueError, OSError) as exc:
            details.append(f"skipped {name}: {exc}")
            continue
        n, det = apply_status_checks(checks, listings, today)
        total += n
        details.extend(f"{name}: {d}" for d in det)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(f"applied {today}; {n} flag change(s)\n")
    return total, details


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
        if old.get("change_flag") in GONE_FLAGS:
            old["last_seen"] = old.get("last_seen", today)
            carried.append(old)
            continue
        old["change_flag"] = old.get("departed_as") or "WITHDRAWN"
        old["departed_on"] = today
        carried.append(old)
    return new_listings, carried


def _has_price_number(rec):
    """True if a record carries an actual price number (bounds or digits in text),
    as opposed to a bare 'Auction'/'Contact Agent' placeholder."""
    if rec.get("price_min") is not None or rec.get("price_max") is not None:
        return True
    return any(ch.isdigit() for ch in (rec.get("price_guide_text") or ""))


# Enrichment fields a saved-search alert email never supplies, so a re-ingested
# alert must not blank them out on an already-enriched listing.
_ENRICH_FIELDS = (
    "cover_image", "beds", "baths", "parking", "property_type",
    "description", "features", "floor", "internal_m2",
)


def _preserve_enrichment(new_rec, old_rec):
    """Carry previously-discovered data from old_rec onto new_rec where the new
    (email-derived) record left a gap. Price is the priority: a mined hidden guide
    must survive an auction re-list that arrives with no number."""
    # Price: keep the old number if the new record has none. This is the rule that
    # stops a no-price auction alert reverting a discovered hidden price.
    if _has_price_number(old_rec) and not _has_price_number(new_rec):
        new_rec["price_guide_text"] = old_rec.get("price_guide_text")
        new_rec["price_min"] = old_rec.get("price_min")
        new_rec["price_max"] = old_rec.get("price_max")
    # Other enrichment: fill only where the new record is missing the field.
    for f in _ENRICH_FIELDS:
        if new_rec.get(f) in (None, "", []) and old_rec.get(f) not in (None, "", []):
            new_rec[f] = old_rec[f]
    # Prefer an already-resolved direct listing URL over an alert/search URL.
    old_url, new_url = old_rec.get("url", ""), new_rec.get("url", "")
    if old_url and ("/results" in new_url or "excludeunderoffer" in new_url or not new_url):
        new_rec["url"] = old_url


def age_change_flags(listings, today):
    """Called ONCE per refresh (/api/refresh, gmail_fetch CLI), BEFORE the merge,
    on the prior watchlist. Increments `flag_sweeps` on every record carrying a
    transient flag and reverts the flag to UNCHANGED once it has survived
    FLAG_SWEEP_LIFETIME refreshes. Records the merge then re-flags get
    flag_sweeps=0 again. Returns the number of flags cleared.

    Bug fixed 1 Oct 2026: previously nothing ever cleared NEW - merge_incremental
    only touched listings present in the incoming alert batch, and alerts are
    new-only, so a NEW listing that never re-appeared kept NEW for ever (20 such
    records dating back to 21 Jun). Legacy records with no counter whose
    last_seen is before today are treated as already expired."""
    cleared = 0
    for l in listings:
        flag = l.get("change_flag")
        if flag not in TRANSIENT_FLAGS:
            l.pop("flag_sweeps", None)
            continue
        n = l.get("flag_sweeps")
        if not isinstance(n, int):
            # Legacy record (pre-counter): stale if not sighted today.
            n = FLAG_SWEEP_LIFETIME - 1 if (l.get("last_seen") or "") < today else 0
        n += 1
        if n >= FLAG_SWEEP_LIFETIME:
            l["change_flag"] = "UNCHANGED"
            l["flag_cleared_on"] = today
            l.pop("flag_sweeps", None)
            cleared += 1
        else:
            l["flag_sweeps"] = n
    return cleared


def merge_incremental(new_scored, prior_listings, today):
    """Merge mode for email-alert ingestion (route A). Alert emails list only NEW
    matches, not the full current field, so we MUST NOT infer withdrawals from
    absence. We union the new listings onto the existing watchlist: add NEW ones,
    update price/open-home changes on existing ones, and leave everything else
    untouched (preserving status/notes). Departures (SOLD/UNDER_OFFER/WITHDRAWN)
    come from three explicit sources, never from absence: (a) sold-alert emails
    (gmail_fetch.apply_departures), (b) the bookmarklet's status-banner read
    (listing_status on the incoming record), and (c) Adam's manual marking in
    the drawer. A record already flagged gone is NEVER resurrected by a re-read
    alert email (the 3-day IMAP window re-serves pre-sale alerts); only an
    explicit listing_status='on_market' from a fresh bookmarklet page read (or
    a manual re-mark) revives it."""
    merged = {listing_key(l): l for l in prior_listings}
    stubs = []
    for l in new_scored:
        k = listing_key(l)
        old = merged.get(k)
        if is_status_stub(l):
            # A status-only record (url + listing_status) from the sweep's
            # verification leg: route to apply_status_checks so it updates the
            # tracked record's flag instead of overwriting the whole record.
            stubs.append(l)
            continue
        l.setdefault("first_seen", today)
        l["last_seen"] = today
        incoming_status = (l.get("listing_status") or "").strip().lower()
        if old is not None and old.get("change_flag") in GONE_FLAGS:
            # The watchlist says this one is gone. A stale alert email cannot
            # bring it back - but a bookmarklet click on a live page can.
            if incoming_status == "on_market":
                old["change_flag"] = "UNCHANGED"
                old.pop("departed_on", None)
                old["relisted_on"] = today
                old["last_seen"] = today
            else:
                old["last_seen"] = today
            continue
        if old is None:
            l["change_flag"] = "NEW"
            l["flag_sweeps"] = 0
        else:
            l["first_seen"] = old.get("first_seen", today)
            # Alert emails carry only URL/price/suburb; an auction re-list usually
            # arrives with NO number. Never let that revert a price (or other
            # enrichment) we have already discovered: carry the prior record's
            # enriched fields onto the new one where the email left a gap. The
            # discovered hidden price stays "over the top of" the no-price original.
            _preserve_enrichment(l, old)
            flag = "UNCHANGED"
            if (l.get("price_min") != old.get("price_min")
                    or l.get("price_max") != old.get("price_max")):
                flag = "PRICE_CHANGED"
                l["prior_price_text"] = old.get("price_guide_text")
            elif set(l.get("open_homes", [])) - set(old.get("open_homes", [])):
                flag = "OPEN_HOME_ADDED"
            if flag == "UNCHANGED" and old.get("change_flag") in TRANSIENT_FLAGS:
                # Re-served alert (3-day IMAP window) with nothing new: keep the
                # still-live flag and its age rather than clearing it early.
                flag = old["change_flag"]
                if "flag_sweeps" in old:
                    l["flag_sweeps"] = old["flag_sweeps"]
            elif flag != "UNCHANGED":
                l["flag_sweeps"] = 0
            else:
                l.pop("flag_sweeps", None)
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
        # A departure read from the incoming record itself (bookmarklet banner /
        # Claude sweep enrichment) takes effect on the way in.
        apply_listing_status(l, today)
        merged[k] = l
    out = list(merged.values())
    if stubs:
        apply_status_checks(stubs, out, today)
    return out


_NOTE_ID_RE = re.compile(r"-(\d{7,})(?:[/?#]|$)")


def _note_identity(url_or_key):
    """Stable identities for a note key (a listing URL or 'address|suburb'):
    the portal's numeric listing id (survives Domain/REA URL moves such as
    /sold/) and a normalised street address (from a Domain search URL's
    street= parameter, or the 'address|suburb' fallback key)."""
    from urllib.parse import urlparse, parse_qs
    ids = set()
    k = url_or_key or ""
    m = _NOTE_ID_RE.search(k)
    if m:
        ids.add("id:" + m.group(1))
    if k.startswith("http"):
        street = parse_qs(urlparse(k).query).get("street")
        if street:
            ids.add("addr:" + " ".join(street[0].lower().split()))
    elif "|" in k:
        ids.add("addr:" + " ".join(k.replace("|", " ").lower().split()))
    return ids


def _listing_identity(l):
    ids = _note_identity(l.get("url") or "")
    addr = " ".join(f"{l.get('address') or ''} {l.get('suburb') or ''}".lower().split())
    if addr:
        ids.add("addr:" + addr)
    return ids


def migrate_note_keys(listings, notes):
    """Re-key orphaned notes onto their listing's current key. Notes are keyed
    by listing URL, but the URL changes (search URL -> direct URL on
    enrichment; REA moves to /sold/), which silently detached Adam's status
    (e.g. a 'rejected' mark) from the listing. Returns True if notes changed.
    Only moves a note when exactly one listing matches and that listing has
    no note of its own, so nothing is ever overwritten."""
    current = {listing_key(l) for l in listings}
    by_ident = {}
    for l in listings:
        for i in _listing_identity(l):
            by_ident.setdefault(i, set()).add(listing_key(l))
    changed = False
    for old_key in [k for k in notes if k not in current]:
        targets = set()
        for i in _note_identity(old_key):
            targets |= by_ident.get(i, set())
        if len(targets) == 1:
            new_key = targets.pop()
            if new_key not in notes:
                notes[new_key] = notes.pop(old_key)
                changed = True
    return changed


def carry_notes(listings, notes_path):
    if not os.path.exists(notes_path):
        return
    try:
        with open(notes_path, "r", encoding="utf-8") as fh:
            notes = json.loads(fh.read())
    except (ValueError, OSError):
        return
    if isinstance(notes, dict) and migrate_note_keys(listings, notes):
        try:
            with open(notes_path, "w", encoding="utf-8") as fh:
                json.dump(notes, fh, indent=2, ensure_ascii=False)
        except OSError:
            pass
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


def is_empty_listing(l):
    """True for a placeholder shell carrying no usable identity: no address AND no
    bedroom count AND no price (neither numeric bounds nor a digit in the guide text).
    These come from alert parsing that found a link/fragment but no real data, and
    should be flushed rather than shown."""
    if (l.get("address") or "").strip():
        return False
    if l.get("beds"):                       # any positive bedroom count
        return False
    if l.get("price_min") or l.get("price_max"):
        return False
    if any(ch.isdigit() for ch in (l.get("price_guide_text") or "")):
        return False
    return True


def build_counts(listings):
    """Header counts. Takes the FULL listing set (active + departed): the
    active-market counts (total/tier1_pass/new/price_changed) are computed over
    the active subset, while sold/under_offer/withdrawn count the departed
    records themselves. (The old version was handed the pre-filtered active
    list, so sold/withdrawn could never be non-zero - fixed with the
    sold-detection work, Jul 2026.)"""
    active = [l for l in listings if l.get("change_flag") not in GONE_FLAGS]
    return {
        "total": len(active),
        "tier1_pass": sum(1 for l in active if l.get("tier1", {}).get("pass")),
        "new": sum(1 for l in active if l.get("change_flag") == "NEW"),
        "price_changed": sum(1 for l in active if l.get("change_flag") == "PRICE_CHANGED"),
        "sold": sum(1 for l in listings if l.get("change_flag") == "SOLD"),
        "under_offer": sum(1 for l in listings if l.get("change_flag") == "UNDER_OFFER"),
        "withdrawn": sum(1 for l in listings if l.get("change_flag") == "WITHDRAWN"),
    }


def main(argv):
    ap = argparse.ArgumentParser(description="Score + diff + write a dashboard sweep.")
    ap.add_argument("harvest", nargs="?", default=None,
                    help="harvest JSON from the Claude-in-Chrome Domain sweep "
                         "(optional when only --status-file / --worklist is used)")
    ap.add_argument("--osm", default=os.path.join(DATA, "osm_amenities.geojson"))
    ap.add_argument("--out", default=os.path.join(DATA, "listings.json"))
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--incremental", action="store_true",
                    help="merge into the existing listings.json instead of replacing "
                         "(use for email-alert ingestion - alerts are new-only).")
    # --- status verification leg ---
    ap.add_argument("--worklist", nargs="?", const=DEFAULT_WORKLIST_CAP, type=int,
                    metavar="N",
                    help="write data/status-worklist-YYYYMMDD.json listing the N active "
                         "listings whose market status most needs a page re-read, "
                         "then exit (no write to listings.json).")
    ap.add_argument("--worklist-all", action="store_true",
                    help="with --worklist: include every active listing, not just the "
                         "stale/auction-passed ones.")
    ap.add_argument("--status-file", action="append", default=[], metavar="PATH",
                    help="status-check JSON (page re-read results) to apply; repeatable. "
                         "Files dropped in data/status-checks/ are applied automatically.")
    ap.add_argument("--no-status-dir", action="store_true",
                    help="do not auto-apply pending files from data/status-checks/.")
    ap.add_argument("--probe", nargs="?", const=-1, type=int, metavar="N",
                    help="fetch the worklist pages (status_probe.py) and apply what they say; "
                         "N pages (default status_probe.DEFAULT_CAP).")
    args = ap.parse_args(argv[1:])

    syd = now_sydney()
    today = syd.date().isoformat()
    checks_dir = os.path.join(DATA, "status-checks")

    # --worklist: emit the verification worklist and stop.
    if args.worklist is not None:
        prior_listings = []
        if os.path.exists(args.out):
            with open(args.out, "r", encoding="utf-8") as fh:
                prior_listings = json.loads(fh.read()).get("listings", [])
        work = build_status_worklist(prior_listings, today, cap=args.worklist,
                                     include_all=args.worklist_all)
        wl_path = os.path.join(DATA, "status-worklist-" + today.replace("-", "") + ".json")
        with open(wl_path, "w", encoding="utf-8") as fh:
            json.dump({"generated_on": today, "count": len(work),
                       "instructions": ("Open each url in Claude-in-Chrome (individual listing "
                                        "page only), read the page's own status banner, and "
                                        "write data/status-checks/status-" + today.replace("-", "")
                                        + ".json as {\"checks\": [{url, listing_status, "
                                        "status_basis, final_url?}]}. listing_status in: sold | "
                                        "under_offer | withdrawn | not_found | on_market | unknown."),
                       "worklist": work}, fh, indent=2, ensure_ascii=False)
        active_n = sum(1 for l in prior_listings if l.get("change_flag") not in GONE_FLAGS)
        print(f"Wrote {wl_path}: {len(work)} of {active_n} active listings queued "
              f"({sum(1 for w in work if w['priority']==0)} auction-passed, "
              f"{sum(1 for w in work if w['priority']==1)} open-homes-passed, "
              f"{sum(1 for w in work if w['priority']==2)} stale).")
        return 0

    harvest = {}
    listings = []
    if args.harvest:
        with open(args.harvest, "r", encoding="utf-8") as fh:
            harvest = json.loads(fh.read())
        listings = harvest["listings"] if isinstance(harvest, dict) else harvest
        if not isinstance(harvest, dict):
            harvest = {}
    elif not args.status_file and args.no_status_dir and args.probe is None:
        ap.error("nothing to do: give a harvest file, --status-file, --probe, or --worklist")
    if not args.harvest and not args.incremental:
        # Status-only runs must never be mistaken for a full-snapshot sweep
        # (which would mark every listing WITHDRAWN by absence).
        args.incremental = True

    # (1) score
    if os.path.exists(args.osm):
        amenities = score_mod.load_amenities(args.osm)
    else:
        print(f"WARNING: {args.osm} missing - catchments will be unknown.", file=sys.stderr)
        amenities = {c: [] for c in score_mod.CATCHMENT_CLASSES}
    for l in listings:
        score_mod.score_listing(l, amenities)

    # (2) diff / merge
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
        # Explicit per-listing status from the harvest (Claude read the page's
        # sold/under-offer banner during enrichment) overrides presence-derived flags.
        for l in active:
            apply_listing_status(l, today)

    # (2b) status verification: apply page re-read results to the watchlist.
    # Explicit --status-file(s) first, then anything pending in data/status-checks/.
    status_changed, status_details = 0, []
    pool = active + carried
    for sf in args.status_file:
        n, det = apply_status_checks(load_status_checks(sf), pool, today)
        status_changed += n
        status_details.extend(f"{os.path.basename(sf)}: {d}" for d in det)
    if not args.no_status_dir:
        n, det = apply_pending_status_files(pool, checks_dir, today)
        status_changed += n
        status_details.extend(det)
    if args.probe is not None:
        import status_probe
        cap = status_probe.DEFAULT_CAP if args.probe < 0 else args.probe
        res = status_probe.run_verification(
            pool, today, cap=cap, log=lambda s: print(s, file=sys.stderr, flush=True))
        status_probe.archive_checks(res["checks"],
                                    f"probe {today}: {res['probed']} probed, {res['changed']} "
                                    f"change(s), verdicts {res['verdicts']}",
                                    syd.strftime("%Y%m%dT%H%M"))
        status_changed += res["changed"]
        status_details.extend("probe: " + d for d in res["details"])
    if status_details:
        print("Status verification:", file=sys.stderr)
        for d in status_details:
            print("  " + d, file=sys.stderr)
    # A record newly flagged gone in `active` must be kept (all_listings below
    # filters `carried` by GONE_FLAGS but keeps every `active` record), and a
    # carried record revived to on-market must move back into `active`.
    revived = [c for c in carried if c.get("change_flag") not in GONE_FLAGS]
    if revived:
        active = active + revived
        carried = [c for c in carried if c.get("change_flag") in GONE_FLAGS]

    # (3) notes
    carry_notes(active + carried, os.path.join(DATA, "notes.json"))

    # (3b) flush empty placeholder shells (no address / price / beds)
    active = [l for l in active if not is_empty_listing(l)]
    carried = [l for l in carried if not is_empty_listing(l)]

    # (4) assemble + write
    all_listings = active + [c for c in carried if c.get("change_flag") in GONE_FLAGS]
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
        "counts": build_counts(all_listings),
        "listings": all_listings,
    }
    os.makedirs(snap_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    snap_name = syd.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M") + "Z.json"
    with open(os.path.join(snap_dir, snap_name), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)

    c = out["counts"]
    print("Wrote " + args.out + " (" + str(c['total']) + " active, "
          + str(c['tier1_pass']) + " Tier-1 pass; " + str(c['sold']) + " sold, "
          + str(c['under_offer']) + " under offer, " + str(c['withdrawn']) + " withdrawn; "
          + str(status_changed) + " status change(s) from verification) and snapshot " + snap_name)

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
