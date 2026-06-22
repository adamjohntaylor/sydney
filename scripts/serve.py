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

# Import scoring and geocoding modules
sys.path.insert(0, HERE)
import score as score_mod
import geocode as geocode_mod
import gmail_fetch as gmail_mod


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DASH_DIR, **kw)

    def _json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/health":
            return self._json(200, {"ok": True, "served": True, "refresh_available": True})
        return super().do_GET()

    def _handle_push(self):
        """Commit and push changes to GitHub."""
        import subprocess
        try:
            # Check if there are changes
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=DASH_DIR, capture_output=True, text=True
            )
            if not status.stdout.strip():
                return self._json(200, {"ok": True, "message": "Nothing to push - already up to date"})

            # Add all changes
            subprocess.run(["git", "add", "-A"], cwd=DASH_DIR, check=True)

            # Commit
            subprocess.run(
                ["git", "commit", "-m", "Update listings from dashboard"],
                cwd=DASH_DIR, check=True
            )

            # Push
            result = subprocess.run(
                ["git", "push"],
                cwd=DASH_DIR, capture_output=True, text=True
            )
            if result.returncode != 0:
                return self._json(500, {"ok": False, "error": result.stderr})

            return self._json(200, {"ok": True, "message": "Pushed to GitHub"})
        except subprocess.CalledProcessError as e:
            return self._json(500, {"ok": False, "error": str(e)})
        except Exception as e:
            return self._json(500, {"ok": False, "error": str(e)})

    def _handle_refresh(self):
        """Fetch new emails, geocode missing coords, and re-score all listings."""
        try:
            # First, try to fetch new listings from Gmail
            new_from_email = 0
            gmail_error = None
            if os.path.exists(gmail_mod.IMAP_CREDS_PATH):
                try:
                    emails = gmail_mod.fetch_via_imap(days_back=3)
                    if emails:
                        parsed = gmail_mod.parse_emails_for_listings(emails)
                        if parsed:
                            new_from_email = gmail_mod.merge_new_listings(parsed, dry_run=False)
                except Exception as e:
                    gmail_error = str(e)

            # Load listings (may have been updated by gmail fetch)
            with open(LISTINGS_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            listings = data.get("listings", [])

            # Geocode any missing
            geocoded_count, geocode_fails = geocode_mod.geocode_listings(listings)

            # Load amenities and re-score
            if os.path.exists(OSM_PATH):
                amenities = score_mod.load_amenities(OSM_PATH)
            else:
                amenities = {c: [] for c in score_mod.CATCHMENT_CLASSES}

            for l in listings:
                score_mod.score_listing(l, amenities)

            # Update counts
            active = [l for l in listings if l.get("change_flag") not in ("WITHDRAWN", "SOLD")]
            data["counts"]["tier1_pass"] = sum(1 for l in active if l.get("tier1", {}).get("pass"))

            # Write back
            with open(LISTINGS_PATH, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)

            already_geocoded = sum(1 for l in listings if l.get("lat") and l.get("lon")) - geocoded_count
            result = {
                "ok": True,
                "new_from_email": new_from_email,
                "newly_geocoded": geocoded_count,
                "already_geocoded": already_geocoded,
                "geocode_failed": geocode_fails,
                "total": len(listings),
                "rescored": len(listings),
                "tier1_pass": data["counts"]["tier1_pass"]
            }
            if gmail_error:
                result["gmail_error"] = gmail_error
            return self._json(200, result)
        except Exception as exc:
            return self._json(500, {"ok": False, "error": str(exc)})

    def do_POST(self):
        if self.path == "/api/refresh":
            return self._handle_refresh()
        if self.path == "/api/push":
            return self._handle_push()
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
            return self._json(200, {"ok": True, "count": len(notes)})
        except Exception as exc:  # noqa: BLE001
            return self._json(400, {"ok": False, "error": str(exc)})

    def end_headers(self):
        # never cache data files - the dashboard must see the latest sweep
        if self.path.startswith("/data/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # quiet


def main():
    os.makedirs(os.path.dirname(NOTES_PATH), exist_ok=True)
    if not os.path.exists(NOTES_PATH):
        with open(NOTES_PATH, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Live-properties dashboard serving at  http://localhost:{PORT}/")
    print(f"Serving folder: {DASH_DIR}")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
