#!/usr/bin/env python3
"""
serve.py - tiny local server for the live-properties dashboard.

A browser cannot write to disk from a file:// page, so notes annotations need a
local endpoint to persist. This stdlib-only server (no pip installs) serves the
dashboard folder and accepts note saves.

Run:
    cd D:\\Projects\\Sydney\\dashboard
    python scripts\\serve.py
then open  http://localhost:8777/  in your browser.

Endpoints:
    GET  /                -> index.html
    GET  /data/...        -> static data files (listings.json, notes.json, ...)
    POST /api/save-notes  -> overwrites data/notes.json with the posted JSON body
    POST /api/refresh     -> geocode missing coords + re-score all listings
    GET  /api/health      -> {"ok": true, "refresh_available": true}

The dashboard auto-detects whether it is being served (notes save to disk) or
opened as a bare file (notes export as a downloadable notes.json instead).
"""

from __future__ import annotations
import json
import os
import sys
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

PORT = int(os.environ.get("DASHBOARD_PORT", "8777"))
HERE = os.path.dirname(os.path.abspath(__file__))
DASH_DIR = os.path.normpath(os.path.join(HERE, ".."))
NOTES_PATH = os.path.join(DASH_DIR, "data", "notes.json")
LISTINGS_PATH = os.path.join(DASH_DIR, "data", "listings.json")
OSM_PATH = os.path.join(DASH_DIR, "data", "osm_amenities.geojson")

# Import scoring, geocoding, and sweep modules
sys.path.insert(0, HERE)
import score as score_mod
import geocode as geocode_mod
import gmail_fetch as gmail_mod
import sweep as sweep_mod
import parse_alert_email as alert_mod
import datetime as dt

SNAP_DIR = os.path.join(DASH_DIR, "data", "snapshots")
SHORTLIST_PATH = os.path.join(DASH_DIR, "..", "07-property-shortlist.md")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DASH_DIR, **kw)

    def _json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # CORS headers for bookmarklet
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, BrokenPipeError) as exc:
            # The browser closed the connection before we replied - common for the
            # slow /api/push (network round-trip to GitHub) if Push is clicked more
            # than once or the page reloads mid-request. The action already ran
            # server-side; log a one-liner instead of a noisy unhandled traceback.
            sys.stderr.write(f"[client disconnected before response: {exc}]\n")
            sys.stderr.flush()

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/health":
            return self._json(200, {"ok": True, "served": True, "refresh_available": True})
        return super().do_GET()

    def _commit_and_push(self):
        """Commit and push changes to GitHub. Returns (ok: bool, message: str).

        Non-fatal helper: callers (e.g. enrichment) can report a push failure
        without losing the local save that already succeeded.
        """
        import subprocess
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=DASH_DIR, capture_output=True, text=True
            )
            if not status.stdout.strip():
                return True, "Nothing to push - already up to date"

            subprocess.run(["git", "add", "-A"], cwd=DASH_DIR, check=True)
            subprocess.run(
                ["git", "commit", "-m", "Update listings from dashboard"],
                cwd=DASH_DIR, check=True
            )
            result = subprocess.run(
                ["git", "push"],
                cwd=DASH_DIR, capture_output=True, text=True
            )
            if result.returncode != 0:
                return False, (result.stderr.strip() or "git push failed")
            return True, "Pushed to GitHub"
        except subprocess.CalledProcessError as e:
            return False, str(e)
        except Exception as e:
            return False, str(e)

    def _handle_push(self):
        """Commit and push changes to GitHub (manual 'Push to GitHub' button)."""
        ok, msg = self._commit_and_push()
        if ok:
            return self._json(200, {"ok": True, "message": msg})
        return self._json(500, {"ok": False, "error": msg})

    def _handle_refresh(self):
        """Full refresh: fetch emails, geocode, score, detect changes, archive snapshot."""
        sys.stderr.write("\n*** REFRESH HANDLER CALLED ***\n")
        sys.stderr.flush()
        try:
            print("\n=== REFRESH STARTED ===", file=sys.stderr, flush=True)
            syd = sweep_mod.now_sydney()
            today = syd.date().isoformat()

            # Step 1: Fetch new listings from Gmail
            print("Step 1: Fetching Gmail alerts...", file=sys.stderr, flush=True)
            new_from_email = 0
            gmail_error = None
            new_listings_raw = []
            if os.path.exists(gmail_mod.IMAP_CREDS_PATH):
                try:
                    emails = gmail_mod.fetch_via_imap(days_back=3)
                    if emails:
                        new_listings_raw = gmail_mod.parse_emails_for_listings(emails)
                except Exception as e:
                    gmail_error = str(e)

            # Step 2: Load existing listings
            print(f"Step 2: Loading existing listings...", file=sys.stderr, flush=True)
            prior_listings = []
            if os.path.exists(LISTINGS_PATH):
                with open(LISTINGS_PATH, "r", encoding="utf-8") as fh:
                    prior_data = json.load(fh)
                prior_listings = prior_data.get("listings", [])

            # Step 3: Geocode new listings
            print(f"Step 3: Geocoding {len(new_listings_raw)} new listings...", file=sys.stderr, flush=True)
            if new_listings_raw:
                geocode_mod.geocode_listings(new_listings_raw, max_per_run=10)

            # Step 4: Load amenities for scoring
            print("Step 4: Loading amenities...", file=sys.stderr, flush=True)
            if os.path.exists(OSM_PATH):
                amenities = score_mod.load_amenities(OSM_PATH)
            else:
                amenities = {c: [] for c in score_mod.CATCHMENT_CLASSES}

            # Step 5: Score new listings
            print(f"Step 5: Scoring {len(new_listings_raw)} new listings...", file=sys.stderr, flush=True)
            for l in new_listings_raw:
                score_mod.score_listing(l, amenities)

            # Step 6: Merge new into existing (incremental mode - no WITHDRAWN on absence)
            print("Step 6: Merging listings...", file=sys.stderr, flush=True)
            if new_listings_raw:
                all_listings = sweep_mod.merge_incremental(new_listings_raw, prior_listings, today)
                new_from_email = len([l for l in all_listings if l.get("change_flag") == "NEW"]) - \
                                 len([l for l in prior_listings if l.get("change_flag") == "NEW"])
                new_from_email = max(0, new_from_email)
            else:
                all_listings = prior_listings

            # Step 7: Re-geocode any still missing coords
            print(f"Step 7: Re-geocoding missing coords...", file=sys.stderr, flush=True)
            geocoded_count, geocode_fails = geocode_mod.geocode_listings(all_listings, max_per_run=10)

            # Step 8: Carry forward notes + manual accessibility override FIRST,
            # so the override feeds the re-score below.
            print("Step 8: Carrying forward notes...", file=sys.stderr, flush=True)
            sweep_mod.carry_notes(all_listings, NOTES_PATH)

            # Step 9: Re-score all listings
            print(f"Step 9: Re-scoring {len(all_listings)} listings...", file=sys.stderr, flush=True)
            for l in all_listings:
                score_mod.score_listing(l, amenities)

            # Step 9b: Remove duplicates (by address+suburb, keeping best data)
            print("Step 9b: Removing duplicates...", file=sys.stderr, flush=True)
            seen = {}
            no_address = []  # Keep listings without addresses
            for l in all_listings:
                addr = l.get("address", "").lower().strip()
                suburb = l.get("suburb", "").lower().strip()
                if not addr:
                    no_address.append(l)
                    continue
                key = f"{addr}|{suburb}"

                if key not in seen:
                    seen[key] = l
                else:
                    # Keep the one with better data (photo, direct URL, beds info)
                    existing = seen[key]
                    existing_score = sum([
                        10 if existing.get("cover_image") else 0,
                        5 if existing.get("url") and "excludeunderoffer" not in existing.get("url", "") else 0,
                        2 if existing.get("beds") else 0,
                        1 if existing.get("price_guide_text") else 0,
                    ])
                    new_score = sum([
                        10 if l.get("cover_image") else 0,
                        5 if l.get("url") and "excludeunderoffer" not in l.get("url", "") else 0,
                        2 if l.get("beds") else 0,
                        1 if l.get("price_guide_text") else 0,
                    ])
                    if new_score > existing_score:
                        seen[key] = l
                        print(f"  Replaced duplicate: {addr}, {suburb}", file=sys.stderr, flush=True)
                    else:
                        print(f"  Removed duplicate: {addr}, {suburb}", file=sys.stderr, flush=True)

            dupes_removed = len(all_listings) - len(seen) - len(no_address)
            all_listings = list(seen.values()) + no_address
            if dupes_removed:
                print(f"  Removed {dupes_removed} duplicates", file=sys.stderr, flush=True)

            # Step 9c: Normalize Auction prices
            for l in all_listings:
                price = l.get("price_guide_text", "").strip().lower()
                # If it's just "Auction" or similar without a price guide
                if price and "auction" in price and not any(c.isdigit() for c in price):
                    l["price_guide_text"] = "Auction - No price guide offered"

            # Step 10: Build output data
            print("Step 10: Building output...", file=sys.stderr, flush=True)
            active = [l for l in all_listings if l.get("change_flag") not in ("WITHDRAWN", "SOLD")]
            out = {
                "schema_version": 1,
                "generated_at": syd.astimezone(dt.timezone.utc).isoformat(),
                "generated_at_sydney": syd.strftime("%Y-%m-%d %H:%M %Z (Sydney)"),
                "sweep_provenance": "Refresh via local dashboard server (gmail + geocode + score).",
                "budget_ceiling": score_mod.BUDGET_CEILING,
                "target_area": "Inner West incl. Drummoyne north of Victoria Rd (decision #15).",
                "counts": sweep_mod.build_counts(active),
                "listings": all_listings,
            }

            # Step 11: Write listings.json
            print("Step 11: Writing listings.json...", file=sys.stderr, flush=True)
            with open(LISTINGS_PATH, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2, ensure_ascii=False)

            # Step 12: Archive snapshot
            print("Step 12: Archiving snapshot...", file=sys.stderr, flush=True)
            os.makedirs(SNAP_DIR, exist_ok=True)
            snap_name = syd.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M") + "Z.json"
            with open(os.path.join(SNAP_DIR, snap_name), "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2, ensure_ascii=False)

            # Step 13: Regenerate 07-property-shortlist.md
            print("Step 13: Regenerating shortlist...", file=sys.stderr, flush=True)
            shortlist_updated = False
            try:
                import render as render_mod
                md = render_mod.render(out)
                with open(SHORTLIST_PATH, "w", encoding="utf-8") as fh:
                    fh.write(md)
                shortlist_updated = True
            except Exception:
                pass  # render is optional

            already_geocoded = sum(1 for l in all_listings if l.get("lat") and l.get("lon")) - geocoded_count
            print(f"=== REFRESH COMPLETE === ({len(all_listings)} listings, {out['counts']['tier1_pass']} T1 pass)", file=sys.stderr, flush=True)
            result = {
                "ok": True,
                "new_from_email": new_from_email,
                "newly_geocoded": geocoded_count,
                "already_geocoded": already_geocoded,
                "geocode_failed": geocode_fails,
                "duplicates_removed": dupes_removed,
                "total": len(all_listings),
                "rescored": len(all_listings),
                "tier1_pass": out["counts"]["tier1_pass"],
                "snapshot": snap_name,
                "shortlist_updated": shortlist_updated
            }
            if gmail_error:
                result["gmail_error"] = gmail_error
            return self._json(200, result)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return self._json(500, {"ok": False, "error": str(exc)})

    def _handle_enrich_listing(self):
        """Enrich a single listing with data from bookmarklet."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            item = json.loads(raw.decode("utf-8"))

            with open(LISTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            listings = data.get("listings", [])

            # Find matching listing by address or URL
            address = item.get("address", "").lower().strip()
            suburb = item.get("suburb", "").lower().strip()
            listing_url = item.get("url", "")

            target = None
            for l in listings:
                l_addr = l.get("address", "").lower()
                l_suburb = l.get("suburb", "").lower()
                l_url = l.get("url", "")

                # Match by address+suburb or by URL containing same property ID
                if (address and address in l_addr and suburb and suburb == l_suburb):
                    target = l
                    break
                # Also try matching by property ID in URL (the numeric suffix)
                if listing_url and l_url:
                    import re
                    new_id = re.search(r'-(\d{7,12})$', listing_url)
                    old_id = re.search(r'-(\d{7,12})$', l_url)
                    if new_id and old_id and new_id.group(1) == old_id.group(1):
                        target = l
                        break

            if not target:
                # Try looser match - just address
                for l in listings:
                    l_addr = l.get("address", "").lower()
                    if address and address in l_addr:
                        target = l
                        break

            if not target:
                return self._json(404, {
                    "ok": False,
                    "error": f"No matching listing found for {address}, {suburb}"
                })

            # Snapshot the target's Tier 1 BEFORE enrichment, so we can report
            # which criteria the new data resolved (? -> pass/fail).
            before_t1 = dict(target.get("tier1") or {})
            before_unverified = set(before_t1.get("unverified", []))

            # Update listing with enrichment data
            if item.get("url"):
                target["url"] = item["url"]
            if item.get("cover_image"):
                target["cover_image"] = item["cover_image"]
            if item.get("beds") is not None:
                target["beds"] = item["beds"]
            if item.get("baths") is not None:
                target["baths"] = item["baths"]
            if item.get("parking") is not None:
                target["parking"] = item["parking"]
            if item.get("property_type"):
                target["property_type"] = item["property_type"]
            if item.get("description"):
                target["description"] = item["description"]
            if item.get("internal_m2"):
                target["internal_m2"] = item["internal_m2"]
            # Only update price if it's a valid price (contains $ and digits, or specific keywords)
            new_price = item.get("price_guide_text", "")
            if new_price and ("$" in new_price or "contact" in new_price.lower() or "auction" in new_price.lower()):
                target["price_guide_text"] = new_price
                # Parse numeric bounds so the Tier 1 budget criterion can resolve.
                # "Auction"/"Contact Agent" with no number -> Nones, leaving the
                # numeric fields untouched so budget stays an honest "?".
                _ptext, pmin, pmax = alert_mod.parse_price(new_price)
                if pmin is not None:
                    target["price_min"] = pmin
                if pmax is not None:
                    target["price_max"] = pmax

            # Update source to reflect direct listing
            if "domain.com.au" in item.get("url", ""):
                target["source"] = "domain"
            elif "realestate.com.au" in item.get("url", ""):
                target["source"] = "realestate"

            # Carry Adam's notes + manual accessibility override FIRST, so the
            # override feeds scoring on this same pass.
            sweep_mod.carry_notes(listings, NOTES_PATH)

            # Re-score ALL listings (Adam's choice) so the new data flips the
            # Tier 1 marks and any scoring-logic changes propagate globally.
            if os.path.exists(OSM_PATH):
                amenities = score_mod.load_amenities(OSM_PATH)
            else:
                amenities = {c: [] for c in score_mod.CATCHMENT_CLASSES}
            for l in listings:
                score_mod.score_listing(l, amenities)

            # Recompute header counts; preserve the last-sweep metadata
            # (enrichment is not a new sweep, so generated_at_* stay as-is).
            active = [l for l in listings if l.get("change_flag") not in ("WITHDRAWN", "SOLD")]
            data["counts"] = sweep_mod.build_counts(active)

            # Save listings.json
            with open(LISTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            # Regenerate 07-property-shortlist.md so the pushed state is consistent.
            try:
                import render as render_mod
                with open(SHORTLIST_PATH, "w", encoding="utf-8") as fh:
                    fh.write(render_mod.render(data))
            except Exception:
                pass  # render is optional

            # Which Tier 1 criteria did the new data resolve?
            after_t1 = target.get("tier1", {})
            after_unverified = set(after_t1.get("unverified", []))
            after_fails = set(after_t1.get("fails", []))
            resolved = [
                {"criterion": k, "state": ("fail" if k in after_fails else "pass")}
                for k in sorted(before_unverified - after_unverified)
            ]

            # Auto-push to GitHub (non-fatal: local save already succeeded).
            push_ok, push_msg = self._commit_and_push()

            return self._json(200, {
                "ok": True,
                "matched": f"{target.get('address')}, {target.get('suburb')}",
                "enriched": list(item.keys()),
                "rescored": len(listings),
                "tier1_pass": bool(after_t1.get("pass")),
                "tier1_fails": after_t1.get("fails", []),
                "tier1_unverified": after_t1.get("unverified", []),
                "tier1_pass_count": data["counts"]["tier1_pass"],
                "resolved": resolved,
                "pushed": push_ok,
                "push_message": push_msg,
            })
        except Exception as e:
            return self._json(500, {"ok": False, "error": str(e)})

    def _handle_enrich_batch(self):
        """Add URLs to multiple listings at once."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            items = json.loads(raw.decode("utf-8"))

            if not isinstance(items, list):
                return self._json(400, {"ok": False, "error": "Expected array of items"})

            with open(LISTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            listings = data.get("listings", [])
            updated = 0

            for item in items:
                url = item.get("url", "").strip()
                address = item.get("address", "").lower().strip()
                suburb = item.get("suburb", "").lower().strip()

                if not url or not address:
                    continue

                # Find matching listing
                for l in listings:
                    l_addr = l.get("address", "").lower()
                    l_suburb = l.get("suburb", "").lower()
                    if address in l_addr and suburb == l_suburb:
                        l["url"] = url
                        if "domain.com.au" in url:
                            l["source"] = "domain"
                        elif "realestate.com.au" in url:
                            l["source"] = "realestate"
                        updated += 1
                        break

            # Save
            with open(LISTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            return self._json(200, {"ok": True, "count": updated})
        except Exception as e:
            return self._json(500, {"ok": False, "error": str(e)})

    def _apply_notes_and_rescore(self):
        """Apply notes (incl. manual accessibility override) and re-score all
        listings, rewriting listings.json + regenerating 07. Local only, no push.
        Returns the number of listings re-scored (0 if listings.json absent)."""
        if not os.path.exists(LISTINGS_PATH):
            return 0
        with open(LISTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        listings = data.get("listings", [])
        # Carry FIRST so the accessibility override feeds scoring.
        sweep_mod.carry_notes(listings, NOTES_PATH)
        if os.path.exists(OSM_PATH):
            amenities = score_mod.load_amenities(OSM_PATH)
        else:
            amenities = {c: [] for c in score_mod.CATCHMENT_CLASSES}
        for l in listings:
            score_mod.score_listing(l, amenities)
        active = [l for l in listings if l.get("change_flag") not in ("WITHDRAWN", "SOLD")]
        data["counts"] = sweep_mod.build_counts(active)
        with open(LISTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        try:
            import render as render_mod
            with open(SHORTLIST_PATH, "w", encoding="utf-8") as fh:
                fh.write(render_mod.render(data))
        except Exception:
            pass
        return len(listings)

    def do_POST(self):
        if self.path == "/api/refresh":
            return self._handle_refresh()
        if self.path == "/api/push":
            return self._handle_push()
        if self.path == "/api/enrich-batch":
            return self._handle_enrich_batch()
        if self.path == "/api/enrich-listing":
            return self._handle_enrich_listing()
        if self.path != "/api/save-notes":
            return self._json(404, {"ok": False, "error": "unknown endpoint"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            notes = json.loads(raw.decode("utf-8"))
            if not isinstance(notes, dict):
                raise ValueError("notes payload must be a JSON object")
            # atomic-ish write
            tmp = NOTES_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(notes, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, NOTES_PATH)
            # Re-score so a manual accessibility verdict in the notes takes effect
            # immediately (carry_notes applies the override, then score). Local
            # only - notes are personal, so no auto-push here.
            rescored = self._apply_notes_and_rescore()
            return self._json(200, {"ok": True, "count": len(notes), "rescored": rescored})
        except Exception as exc:  # noqa: BLE001
            return self._json(400, {"ok": False, "error": str(exc)})

    def end_headers(self):
        # never cache data files - the dashboard must see the latest sweep
        if self.path.startswith("/data/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        # Log API calls but skip static file requests
        first_arg = str(args[0]) if args else ''
        if '/api/' in first_arg:
            print(f"[{self.log_date_time_string()}] {fmt % args}", file=sys.stderr, flush=True)


def main():
    os.makedirs(os.path.dirname(NOTES_PATH), exist_ok=True)
    if not os.path.exists(NOTES_PATH):
        with open(NOTES_PATH, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Live-properties dashboard serving at  http://localhost:{PORT}/", file=sys.stderr, flush=True)
    print(f"Serving folder: {DASH_DIR}", file=sys.stderr, flush=True)
    print("Press Ctrl+C to stop.", file=sys.stderr, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
