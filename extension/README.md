# Sydney Dashboard Verifier (Chrome extension)

Reads the market status of the listings on the dashboard watchlist **in your own
Chrome session** and posts it to the local dashboard, so sold / under-offer /
withdrawn stock is retired without any scripted fetching (Domain answers scripted
requests with 403/429; it serves your browser normally).

## Install (once)
1. Chrome → `chrome://extensions` → turn on **Developer mode** (top right).
2. **Load unpacked** → choose `D:\Projects\Sydney\dashboard\extension`.
3. Pin the extension (puzzle icon → pin "Sydney Dashboard Verifier").

## Use
1. Serve the dashboard: `python scripts\serve.py` (it must be running on :8777).
2. Click the extension icon → **Verify** (default 60 pages; raise to 300 to clear
   the backlog in one go; tick "include every active listing" for a full pass).
   It opens each worklist page in a background tab, reads it, closes it, and
   posts the verdicts in batches of 20 to `/api/apply-status` (which rewrites
   `listings.json`, archives the checks under `data/status-checks/`, regenerates
   `07`, and pushes). Progress and verdicts show in the popup; the popup can be
   closed and reopened while it runs.
3. Press **Refresh now** on the dashboard to reload.

Ordinary browsing is also useful: any Domain / REA listing page you open that
says **Sold / Under offer / no longer available** is reported to the dashboard
(only departures — live pages are never posted from casual browsing).

## What it reads
- Every rendered listing page also yields the **facts** the watchlist lacks:
  internal floor area (`internal_m2`, Tier 1 needs ≥100 m²), land area
  (`land_m2`, kept separate), beds / baths / parking and property type. For a
  search-URL record that is still for sale and has never had its area read, the
  Verify run opens the resolved listing page too (one extra page per record,
  once). `area_basis` in the record says which page source answered.
- Domain listing page: title `Sold <address> on <date>` ⇒ sold; "Under offer"
  badge ⇒ under offer; "no longer available" / page not found / redirect away
  from the listing id ⇒ withdrawn; otherwise on market (price refreshed).
- REA listing page: `/sold/` URL or JSON-LD `SoldOut` ⇒ sold; same badges.
- Alert-derived single-address **search** URLs (most of the watchlist): the
  for-sale search's result count; if zero, the matching **sold-listings** search
  decides sold (with sale date) vs withdrawn, ignoring sales dated more than
  60 days before the listing was first seen.

Files: `manifest.json` (MV3), `content.js` (page reader), `background.js`
(Verify run + posting), `popup.html/js` (button + log).
