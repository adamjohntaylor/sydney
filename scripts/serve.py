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
    GET  /api/health      -> {"ok": true}

The dashboard auto-detects whether it is being served (notes save to disk) or
opened as a bare file (notes export as a downloadable notes.json instead).
"""

from __future__ import annotations
import json
import os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

PORT = int(os.environ.get("DASHBOARD_PORT", "8777"))
DASH_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
NOTES_PATH = os.path.join(DASH_DIR, "data", "notes.json")


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
            return self._json(200, {"ok": True, "served": True})
        return super().do_GET()

    def do_POST(self):
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
