# Dashboard — Development Log

A record of the engineering work on the live-properties dashboard, written so a
fresh session (or Adam) can reconstruct what exists and why. `SCOPE.md` is the
original spec; `RUNBOOK.md` is the operating procedure; this file is the history
of how the code got to its current shape.

---

## 23 June 2026 — Enrichment now resolves Tier 1 marks (price, bedrooms, accessibility)

Closed the gap where bookmarklet/enrichment data updated a listing's fields but left
the Tier 1 ✓/✗/? marks frozen. Enrichment now re-scores, so newly-known facts resolve
the marks. (Marks resolve to their *true* state — an over-budget guide flips `?`→✗,
not only `?`→✓.)

- **Price** (`serve.py` `_handle_enrich_listing`): the bookmarklet's `price_guide_text`
  is parsed (reusing `parse_alert_email.parse_price`) into numeric `price_min`/`price_max`,
  which the budget criterion reads. "Auction"/"Contact Agent" with no number leave the
  numbers unset, so budget stays an honest `?`.
- **Re-score all + auto-push** (Adam's choices): every enrichment re-scores all listings,
  regenerates `07-property-shortlist.md`, and commits + pushes to GitHub. Push is
  **non-fatal** — a failed push never loses the local save. `_handle_push` was refactored
  to share a `_commit_and_push()` helper.
- **Bedrooms** needed no new code — `beds` is already captured as an integer and read by
  the criterion, so the same re-score resolves it.
- **Accessibility** (`score.py`, `gmail_fetch.py`, `sweep.py`, `index.html`): a deal-breaker
  listings rarely state, so resolved by *confirmation*, not scraping. Three paths, in
  priority: (1) **manual verdict** — new Step-free / Lift controls in the drawer save to
  `notes.json`, applied by `carry_notes` before scoring (authoritative); (2) **filter
  provenance** — REA listings from an accessibility-filtered saved search are tagged
  `accessibility_source="rea_filter"` and scored a *provisional* ✓ (gated by
  `data/accessibility_config.json`, default off); (3) **keyword hint** — a positive phrase
  in the description prompts confirmation but never sets the verdict. `carry_notes` now runs
  *before* scoring in the enrich/refresh/save-notes paths so overrides take effect on the
  same pass; `/api/save-notes` re-scores locally (no push).
- **Verified** via `score.tier1` + `parse_alert_email.parse_price` on crafted cases (all
  branches of budget, bedrooms, accessibility provenance/override/hint, plus the
  decision-#17 warehouse-credit interaction) — all green.

---

## 22 June 2026 — Git/GitHub port, local upgrade, and the enrichment bookmarklet

All of the following happened in a single session on 22 June 2026 (38 commits,
14:42–23:35 AEST). The dashboard had been *built* the day before (decision #26,
21 June); 22 June is when it was put under version control, published, and
substantially extended. Local working tree and `origin/master` are in sync at
commit `626c028`.

### 1. Ported to Git and GitHub Pages

- The `dashboard/` folder became its own Git repository (initial commit `d6dc346`,
  "Initial commit: Sydney property dashboard"). **Note the scope:** the repo root
  is `dashboard/`, *not* the whole `D:\Projects\Sydney\` project — the financial
  models, decision log, and the numbered `01`–`08` documents are deliberately not
  in version control or on the public web.
- Remote: **`https://github.com/adamjohntaylor/sydney.git`**, branch `master`.
- Published via **GitHub Pages**. A `CNAME` file was added then removed
  (`256c962`, "Temporarily remove CNAME to allow github.io access") so the site
  resolves at the default `github.io` address rather than a custom domain. There
  is no `CNAME` in the tree now.
- `.gitignore` keeps secrets out of the repo: `data/.gmail_credentials.json`,
  `.gmail_token.json`, `.gmail_oauth.json`, and `data/.geocode_config.json`
  (which holds the Google Maps API key), plus `__pycache__/`.
- The published static site is read-only: `data/listings.json` and the snapshots
  are committed, so the site shows the latest sweep, but the write-back endpoints
  (notes, refresh, push, enrich) only work when the page is served locally.

### 2. Two-mode architecture (served vs. static)

The dashboard now detects how it is being opened and adapts:

- **Served locally** (`python scripts/serve.py`, `http://localhost:8777/`): notes
  save to disk, and the Refresh / Push / Enrich endpoints are live. The
  "Push to GitHub" button only appears on `localhost`/`127.0.0.1`.
- **Static (GitHub Pages or `file://`)**: the page reads the committed
  `listings.json`, the refresh button degrades to a plain data-reload, and notes
  export as a downloadable `notes.json`. The Refresh handler probes
  `/api/health` for `refresh_available` before attempting a real sweep, so the
  button doesn't error on the public site (`89586c6`).

### 3. The enrichment bookmarklet (the key new tool)

Built because Domain and REA prohibit scraping and block automation — so listing
detail can't be harvested server-side. The bookmarklet moves that extraction into
Adam's own logged-in browser, where the pages are simply *open*. Flow:

1. **Install** — `bookmarklet.html` serves a draggable "Enrich Listing" button.
   The bookmarklet is a one-line loader that injects
   `http://localhost:8777/enrich-bookmarklet.js` (cache-busted with a timestamp)
   into the current page, so the extraction logic can be edited without
   reinstalling the bookmark.
2. **Extract** — `enrich-bookmarklet.js` (329 lines) runs on a Domain or REA
   listing page and pulls: cover image (gallery-first, filtered to the real
   `domainstatic.com.au` / `reastatic.net` CDNs so it never grabs a logo),
   address + suburb (from the page title/heading), beds/baths/parking (feature
   elements → page-text regex → JSON-LD structured data, in that order),
   property type, price, and a 500-char description. **Price extraction is
   layered**: it first mines hidden price fields in the page source
   (`exactPriceV2`, `priceInt`, REA's `marketing_price`, etc.), then visible
   "Price Guide / Offers Over / range" text, then falls back to flagging
   "Auction" or "Contact Agent". A guard rejects any number outside
   $100k–$50M to avoid catching bedroom counts or sold-history figures.
3. **Submit** — the script JSON-encodes the data and opens
   `http://localhost:8777/enrich-submit.html?data=…` in a small popup. Routing
   through the localhost page (rather than POSTing directly) sidesteps the
   HTTPS→HTTP mixed-content block that browsers impose on a `fetch` from the
   HTTPS listing page to the local HTTP server.
4. **Save + match** — `enrich-submit.html` shows the extracted fields, then POSTs
   to `/api/enrich-listing`. The server matches the incoming data to an existing
   listing by address+suburb, then by the numeric property ID in the URL, then by
   a looser address-only match, and merges in the new fields (replacing the
   Domain *search* URL with the *direct* listing URL). Price is only overwritten
   if it looks like a real price.

Bookmarklet-related fixes in the same session: `5d960df` (image extraction +
the HTTPS issue), `95ffaea`/`5d960df` (bogus-price extraction). `42de1f9` is the
feature commit.

### 4. Email-alert ingestion + geocoding upgrades

- **Gmail integration** (`145ab51`, `a54617f`, `901bc57`) — `gmail_fetch.py`
  reads Domain/REA saved-search alert emails (IMAP with an App Password is the
  recommended path; OAuth2 is the alternative), extracts listings by address, and
  merges them. This is route A from decision #27 — ingest the alert emails Adam
  receives rather than scrape. `parse_alert_email.py` is the deterministic
  skeleton-builder (URLs + price + suburb) that Claude then fills out.
- **Search-URL generation** (`b2a1cdd`, `bcaf414`) — because alert emails don't
  always carry a direct listing link, the system auto-generates Domain (and REA)
  search URLs per listing so a card always has something clickable; the
  bookmarklet later replaces these with the real direct URL.
- **Google Maps Geocoding** (`927c064`) — `geocode.py` now prefers the Google
  Maps Geocoding API (fast, no practical rate limit) and falls back to OSM
  Nominatim when no API key is present. The key lives in the git-ignored
  `data/.geocode_config.json`. Earlier in the session `b5d906c` added
  exponential backoff and per-run limits to the Nominatim path.
- **`enrich.py`** (`729ee6c`) — a CLI companion to the bookmarklet for adding
  listing URLs by hand when the browser route isn't convenient.

### 5. The Refresh button became a full pipeline

`serve.py`'s `/api/refresh` (`3066160`, then `901bc57` + `3a5ae66`) now runs an
end-to-end sweep on one click: fetch new Gmail alerts → load existing listings →
geocode the new ones → score them → **incrementally merge** (new-only data never
marks an absent listing WITHDRAWN) → re-geocode anything still missing coords →
re-score everything → carry forward Adam's notes by URL → de-duplicate by
address+suburb (keeping the record with the better photo/URL/beds/price) →
normalise bare "Auction" prices → write `listings.json` + a timestamped snapshot →
**regenerate `../07-property-shortlist.md`**. Detailed step-by-step logging to
stderr was added (`53e3b82`) to make failures legible.

### 6. Push to GitHub button

`/api/push` (`fea3b5d`) lets Adam commit and push the updated `listings.json` from
the dashboard itself: it checks `git status --porcelain`, and if there are
changes, runs `git add -A` → `git commit -m "Update listings from dashboard"` →
`git push`. This is the source of the many identical "Update listings from
dashboard" commits — each is one click of the button after a refresh. The button
is hidden unless the page is served from localhost.

### 7. UI tweaks

- Info icon explaining the Tier 1 / Tier 2 system, made blue/more visible
  (`aa290c1`, `33d55e4`).
- Removed internal documentation from the page footer before publishing
  (`f51c8bc`).
- Improved refresh status messaging (`b318f06`) — the header now reports
  "rescored N, M new from email, K geocoded (T1 pass)".

### Current state (end of 22 June)

`data/listings.json` holds **63 listings, 56 passing Tier 1, 18 flagged NEW**;
last sweep stamped 22 June 23:34 Sydney. `notes.json` has 6 annotations. The
twice-weekly scheduled sweep (Tue + Fri 06:30 Sydney, `sydney-property-sweep`)
remains the automated path; the Refresh button is the manual equivalent.

### Open items (carried from the dashboard build)

- **Walkability is not yet populated** — `build_osm_cache.py` must be run once on
  Adam's own machine (the sandbox can't reach OpenStreetMap) to fill
  `data/osm_amenities.geojson`. Until then catchment ticks are blank.
- **Listing feed depends on Adam's one-time setup** — saved-search alerts on
  Domain/REA with email alerts on, plus a connected Gmail (App Password in
  `data/.gmail_credentials.json`).
- **Google Maps key** is optional but recommended; without it geocoding falls
  back to the rate-limited Nominatim path.
