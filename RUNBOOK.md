# Live Properties Dashboard — Runbook

How a sweep actually runs, how to open the dashboard, and how the pieces fit.
Built from `SCOPE.md` with Adam's §4 resolutions applied.

## TL;DR for Adam

1. **One-time, on your own machine** (the Cowork sandbox can't reach OpenStreetMap):
   ```
   cd D:\Projects\Sydney\dashboard
   python scripts\build_osm_cache.py      # fills data/osm_amenities.geojson; enables walkability
   ```
   Re-run this monthly.
2. **Set up the listing feed (one-time):** create saved-search alerts on Domain
   and realestate.com.au for the criteria (target-area suburbs, 0-$2.2M, 2+ beds,
   apartments + houses), turn on email alerts, and connect **Gmail** or
   **Microsoft 365 (Outlook)** so the sweep can read them. (We ingest alert
   emails, not scraped pages - see "Ingestion" below.)
   - **Optional accessibility shortcut:** on the realestate.com.au saved search,
     also tick the **"step-free entry"** and **"elevator"** accessibility filters,
     then set `"rea_search_has_accessibility_filter": true` in
     `data/accessibility_config.json`. REA-sourced alert listings are then tagged
     `accessibility_source="rea_filter"` and scored as a **provisional** step-free/
     lift pass (the agent tagged the feature; you still verify entry and the
     surrounding terrain at inspection). Domain has no such filter, so Domain
     listings stay `?` until you confirm them. Your manual verdict always wins.
3. **Open the dashboard** (so notes save to disk):
   ```
   python scripts\serve.py                # then open http://localhost:8777/
   ```
   You can also just double-click `index.html`, but in that file-mode notes are
   *downloaded* as `notes.json` for you to save over `data/notes.json` yourself,
   and some browsers block loading the data files from `file://`.
4. **Sweeps run automatically** twice a week (Tue + Fri 06:30 Sydney): they ingest
   new alert emails and merge them into `data/listings.json`. Hit **Refresh now**
   in the header to reload the latest. Each sweep also regenerates
   `../07-property-shortlist.md`.
   - **Zoning is resolved automatically.** When the dashboard is served
     (`serve.py`), **Refresh now** point-queries the NSW DPE Land Zoning layer for
     every warehouse-character listing whose zoning isn't yet checked, fills the
     verdict, and re-scores — so the "zoning unverified" flag clears on its own
     once a verdict comes back (E3/E4 ⇒ Tier 1 fail; R/E1/E2/MU1 ⇒ pass). Already-
     checked listings are skipped, so Refresh never re-hammers the service. In bare
     `file://` mode there's no server to fetch, so zoning stays unverified until
     you serve the dashboard or a scheduled sweep runs.

## Ingestion: email alerts, not scraping (route A, decision #27)

Domain's terms prohibit scraping and they actively block automation, so the sweep
does **not** scrape Domain/REA. Instead Adam sets up **saved-search alert emails**
on Domain and realestate.com.au, and the sweep ingests those emails (content sent
to Adam - legitimate and resilient). One-time Adam setup:
- On Domain and realestate.com.au, create a saved search for the criteria (the
  target-area suburbs, price 0-$2.2M, 2+ beds, apartments + houses) and turn on
  instant/daily email alerts.
- Connect **Gmail** or **Microsoft 365 (Outlook)** so the sweep can read them.

## Why the sweep is "Claude-driven"

The deterministic parts (scoring, catchments, merging, regenerating `07`) are pure
Python. But the rest needs judgement or a live page: reading the alert emails and
extracting listings, opening each individual listing page for full detail,
auto-classifying outlook from description + cover image (Q4), and the NSW zoning
lookup (Q5). So a sweep = **Claude ingests alerts + enriches → writes a harvest
file → `sweep.py` scores/merges/writes**. The scheduled task (`sydney-property-sweep`)
is a self-contained Claude prompt that does exactly this.

## One sweep, step by step

### A. Ingest + enrich (Claude)
1. Read `02-location-and-property-criteria.md`, latest `05-decision-log.md`, and
   this folder first (standing rule, `../CLAUDE.md`).
2. Confirm a Gmail/Outlook connector is available; if not, stop and ask Adam to
   connect one and set up the saved-search alerts (never fall back to scraping).
3. Read the property alert emails received since the last sweep (Domain / REA
   saved-search alerts). Extract every listing; you can pipe a raw email body
   through `python scripts/parse_alert_email.py -` for a deterministic skeleton
   (URLs + price + suburb). De-duplicate by listing URL. If there are no new
   alerts, stop without running the script.
4. For each unique listing, open the **individual listing page** in Claude-in-Chrome
   (a single page, not a search-results scrape) and capture: `address, suburb,
   postcode, price_guide_text, price_min, price_max, property_type, beds, baths,
   parking, internal_m2, strata_pa, agent, agency, cover_image, open_homes[] (next
   14 days, ISO), auction (ISO/null), description, lat, lon`. Also read the page's
   market-status banner and set `listing_status` (`sold` / `under_offer` /
   `withdrawn` / `on_market`) - sweep.py converts it to the matching change flag.
   - `property_type` one of `apartment, house, townhouse, warehouse_conversion`;
     set `is_raw_shell: true` for unconverted shells (Q2 excludes them).
5. **Outlook** (Q4): set `outlook: {class, basis}`, class ∈ `water, park,
   elevated_district, city, leafy, none`, from description + cover image.
6. **Warehouse character**: set `warehouse_character`. If true, **zoning** (Q5):
   `scripts/zoning.py url <lat> <lon>` gives the ArcGIS URL; read its JSON; set
   `zoning` via `zoning.parse_zoning(...)`. E3/E4 ⇒ Tier 1 fail.
7. Write `dashboard/data/harvest-YYYYMMDD.json`:
   `{"generated_at_sydney": "...", "sweep_provenance": "...", "listings": [ ... ]}`.
8. **Verify market status of existing stock (mandatory, every sweep — added
   29 Sep 2026).** Alerts are new-only, so nothing above can discover that a
   listing already on the watchlist has sold or been pulled. **The reader is
   the Chrome extension in `dashboard/extension/`** (install once: see
   `extension/README.md`). With the dashboard served, click the extension icon →
   **Verify**: it opens the worklist pages in background tabs of your own Chrome
   (Domain refuses scripted requests with 403/429 — confirmed 29 Sep 2026 — but
   serves your browser normally), reads each page's own status, and posts the
   verdicts to `/api/apply-status`. For a listing-page URL: Domain's
   `Sold <address> on <date>` title / "Sold by private treaty" stamp ⇒ SOLD; an
   "Under offer" badge ⇒ UNDER_OFFER; 404 / "no longer available" / redirect
   away from the listing id ⇒ WITHDRAWN; REA's move under `/sold/` ⇒ SOLD. For
   the alert-derived single-address *search* URLs (most of the watchlist): the
   for-sale search returning **no exact matches** means the address is gone, and
   the follow-up **sold-listings search** for the same address decides SOLD
   (sale dated after we first saw it) versus WITHDRAWN. Set the page count to
   300 to clear a backlog in one go. Any sold / under-offer / removed listing
   page you open while browsing is reported too.
   *(`scripts/status_probe.py` — the scripted equivalent — is kept but off by
   default: `DASHBOARD_STATUS_PROBE=1` re-enables it on Refresh, `sweep.py
   --probe` / `status_probe.py --cap N` run it by hand; expect `blocked`.)*

   The manual route below remains for pages the probe reports as `blocked` /
   `unknown` (after 3 such reads a record is flagged `needs_manual_check`): run
   `python scripts\sweep.py --worklist` (default 40 pages; `--worklist 80` for a
   bigger bite, `--worklist-all` to include everything). It writes
   `data/status-worklist-YYYYMMDD.json`, prioritised: auction date passed →
   every open home passed → longest unsighted; listings verified in the last 7
   days are skipped. Open **each worklist URL** in Claude-in-Chrome (the listing
   page itself, never a search scrape) and record what the page says as
   `data/status-checks/status-YYYYMMDD.json`:
   ```
   {"checks": [
     {"url": "<worklist url>", "listing_status": "sold",       "status_basis": "banner: Sold 20 Sep 2026"},
     {"url": "...",            "listing_status": "under_offer","status_basis": "banner: Under offer"},
     {"url": "...",            "listing_status": "not_found",  "status_basis": "HTTP 404 / 'no longer available'"},
     {"url": "...",            "listing_status": "on_market",  "status_basis": "banner: For sale; price guide $1.45M", "price_guide_text": "$1,450,000"},
     {"url": "...",            "listing_status": "unknown",    "status_basis": "blocked / captcha"}
   ]}
   ```
   `listing_status` vocabulary: `sold` · `under_offer` · `withdrawn` /
   `not_found` / `removed` / `redirected` / `no_longer_available` (all ⇒
   WITHDRAWN, with the specific evidence kept in `status_basis`) · `on_market`
   · `unknown`. If the page redirected (Domain/REA move sold listings under
   `/sold/`), add `"final_url"`. Do not skip this step because no new alerts
   arrived — the verification leg is what keeps the active list honest.

### B. Score + merge + write (script)
```
python scripts\sweep.py data\harvest-YYYYMMDD.json --incremental
```
`--incremental` MERGES the new listings into the existing `data/listings.json`
(alert emails are new-only, so absence must not mark a listing withdrawn). It
computes catchments + Tier 1 + Tier 2, flags NEW / PRICE_CHANGED / OPEN_HOME_ADDED,
preserves Adam's `notes.json` status/notes by URL, writes `data/listings.json` +
a timestamped snapshot, and regenerates `../07-property-shortlist.md`.

The same run also **applies every not-yet-applied file in `data/status-checks/`**
(a `.applied` marker is written beside each so it is never re-applied), converting
the step-A8 page reads into `SOLD` / `UNDER_OFFER` / `WITHDRAWN` flags with
`status_source="sweep_check"`, `status_basis` and `status_checked_on`. If there
were no new alerts at all, run it with no harvest file:
```
python scripts\sweep.py                     # status-only run (implies --incremental)
python scripts\sweep.py --status-file data\status-checks\status-20260929.json
```
The dashboard's **Refresh now** (`/api/refresh`, Step 6c) applies pending
status files too, and `POST /api/apply-status` accepts the same `{"checks": [...]}`
body directly (it archives the posted checks under `data/status-checks/` for
audit). `GET /api/status-worklist?cap=N` returns the worklist as JSON.

### C. Clean up the inbox (added 1 Oct 2026)
Once `data/listings.json` has been written successfully, the Domain / REA alert
emails the sweep consumed are **moved to Gmail Trash** (Gmail keeps them 30 days,
so a mistake is recoverable from Trash/Bin).
- **Refresh now** (`/api/refresh`, Step 12b) and `python scripts\gmail_fetch.py`
  do this automatically over IMAP, after listings.json + snapshot are on disk.
  The refresh result reports `emails_trashed`, `emails_kept_unparsed` and any
  `email_cleanup_error`. Nothing is deleted if the Gmail step or the write fails.
- **Claude-driven sweep (step A via the Gmail connector):** after step B's script
  has run without error, trash each alert email you read in A3 (Gmail connector
  `trash_message` / `trash_thread`). Never trash before the write succeeds.
- Only messages whose actual sender is `@domain.com.au` / `@realestate.com.au`
  (or a subdomain such as `campaign.realestate.com.au`) are touched.
- An email that yielded **no** listing or sold/under-offer record is **kept** in
  the inbox (a changed email template would otherwise be silently lost) - if
  `emails_kept_unparsed` keeps climbing, the parser needs updating. To trash
  those too, set `DELETE_UNPARSED = True` in `scripts/gmail_fetch.py`.
- To switch clean-up off: `DELETE_AFTER_INGEST = False` in
  `scripts/gmail_fetch.py`, or `gmail_fetch.py --keep-emails` for a single run.

*(Manual full-snapshot mode - drop `--incremental` - is retained for the case
where you ever supply a complete current field; it auto-detects WITHDRAWN/SOLD by
absence. Don't use it with new-only alert data.)*

## Sold / under-offer detection (added 25 Jul 2026)

A listing leaves the active list only on **explicit evidence**, never on absence
from an alert. Three sources set `change_flag` to `SOLD` / `UNDER_OFFER` /
`WITHDRAWN` (all three live in the dashboard's "Sold / under offer / withdrawn"
tab, with `departed_on` / `status_source` / `status_basis` provenance):

1. **Bookmarklet banner read** — every enrichment click on a Domain/REA listing
   page also reads the page's own status banner (Sold / Under offer / no longer
   available) and the server applies it. Clicking the bookmarklet on a listing
   that turns out to be sold is therefore the quickest way to retire it. An
   `on_market` read on a previously departed listing **revives** it
   (`relisted_on`) - the live page is the freshest evidence there is.
   **After pulling this update, re-drag the inline bookmarklet from
   `http://localhost:8777/bookmarklet` - the code is baked into the link.**
2. **Sold-alert emails** — on Domain and realestate.com.au, turn ON the
   "sold / off-market" notifications for the same saved searches (Domain:
   saved-search settings → include sold updates; REA: property update emails).
   Each sweep / Refresh classifies incoming alert mail: sold / under-offer
   notifications are routed to a departure parser and matched against the
   watchlist by listing id → URL → address+suburb. A departure can only flag a
   listing already tracked - digest emails about other properties are ignored -
   and sold emails are never fed to the new-listing parser.
3. **Manual marking** — the detail drawer's "Market status" control
   (`/api/set-market-status`): Sold / Under offer / Withdrawn / On market.
   Use "On market" to restore a listing a fallen-through deal returns.
4. **Sweep verification leg (29 Sep 2026)** — the systematic source. The first
   three are opportunistic (a click, an email that may never have been enabled,
   a hand mark); this one runs every sweep. `sweep.py --worklist` picks the
   active listings most likely to have left the market (auction passed, open
   homes passed, unsighted > 7 days), Claude re-reads those pages, and
   `apply_status_checks` marks them from the page's own evidence. "Evidently
   withdrawn" means page evidence — a 404, "no longer available", a redirect to
   a search/suburb page — never mere absence from an alert. An `on_market` read
   counts as a sighting (`last_seen` refreshed) and revives a departed record; an
   inconclusive read (blocked/captcha/unknown) changes nothing but is counted, and
   after 3 such reads the record is flagged `needs_manual_check`. Each sweep
   takes a capped bite (40 pages) so the backlog clears over successive sweeps
   without hammering the portals; `verification_pending` in the Refresh result
   shows how much remains.

Rules of the state machine: SOLD is terminal (an under-offer email or page read
never downgrades it); a stale re-read alert email never resurrects a departed
listing (the 3-day IMAP window re-serves pre-sale alerts); only an explicit
on-market page read (bookmarklet or verification leg) or your manual re-mark
revives one. A status-only record arriving through a harvest (url +
`listing_status`, no address/beds/price) updates the tracked record's flag and
never overwrites the record. Departed stock is excluded from
the `07` candidate tables and listed in a "Departed from the watchlist" section
at the end.

## The criteria, as encoded (see `scripts/score.py`)

**Tier 1 (pass/fail; `None` = can't tell → flagged, never a silent fail):**
budget ≤ $2.2M · property type (apartment, warehouse-conversion, OR freestanding
house/cottage/semi/terrace/townhouse — decision #28 lifted the ≤2BR-cottage cap;
raw shells excluded; type label matched by token, so "apartment / unit / flat"
resolves) · step-free + lift · beds **≥2 for all types** (decision #28; 3 preferred
for apartments) · transport ≤1.5km · daily supplies ≤1.5km · in target area ·
zoning E1/E2/MU1 for warehouse stock.

**Step-free / lift** resolves in priority order (`score._auto_accessibility`):

1. **Manual verdict** — your Step-free / Lift answer in the drawer (yes/no/unknown,
   saved to `notes.json`). Authoritative; overrides everything below.
2. **Filter provenance** — a listing from an REA search carrying the accessibility
   filters (`accessibility_source="rea_filter"`) → **provisional ✓**.
3. **Auto-detect** from the bookmarklet's structured **features list** (weighted first)
   and the **description**:
   - a `Lift`/`Elevator` feature chip (apartments), or an explicit step-free / level-access /
     wheelchair-access phrase (any type) → **provisional ✓**;
   - a **ground/street-level** dwelling → **provisional ✓** (step-free regardless of a lift);
   - an apartment **lift in context** (`LIFT_CONTEXT_RE` — a building lift as a noun, not the
     verb "lift", not "stairlift"/"facelift"/"uplifting") → **provisional ✓**;
   - a house described **single-level / level-entry** → **provisional ✓**;
   - negatives: an apartment **"no lift" / "walk-up"**, or **stairs-to-entry / steep approach**
     (any type) → **provisional ✗**;
   - positive *and* negative signals colliding → left `?` (ambiguous → needs a look).
4. else `?`, with a soft **keyword hint** in the drawer.

Provisional verdicts (2-3) carry a **basis** string and a "verify entry & terrain at
inspection" note in the drawer; a manual verdict always overrides them. Silence is never a
fail. The **"Needs access check"** filter in the toolbar surfaces every listing whose
step-free/lift is unresolved — i.e. `?` or only provisional (not manually confirmed) — as a
verification worklist.

**Enabling REA filter-provenance (suggestion 6):** on the realestate.com.au saved search add
the **"step-free entry"** and **"elevator"** accessibility filters, then set
`"rea_search_has_accessibility_filter": true` in `data/accessibility_config.json`. It ships
**off** — turning it on before the filters are actually on the saved search would mark every
REA listing a provisional ✓ falsely.

**Enrichment → re-score → publish.** New data injected from the browser bookmarklet
(`/api/enrich-listing`) is merged, the price text is parsed to numeric bounds, **all**
listings are re-scored (so budget/bedrooms/etc. marks resolve), `07` is regenerated,
and the change is committed + pushed to GitHub automatically (push failures are
non-fatal — the local save still stands). Saving an accessibility verdict
(`/api/save-notes`) re-scores locally so the mark updates on reload (no push — notes
are personal). The manual `enrich.py` CLI re-scores on the same shared path.

**Tier 2 (0–100, outlook leading):** outlook 30 · living-area ≥115 m² 20 ·
warehouse-conversion character 12 (only if step-free not failed) · light/aspect 11 ·
pool 9 · parks 9 · restaurants 9 · soft strata penalty up to −10 above ~$12k p.a.
(further-check-A) · pool stays Tier 2 (further-check-B).

## Files
```
dashboard/
  index.html                 dashboard UI (Tailwind CDN + vanilla JS)
  RUNBOOK.md                 this file
  SCOPE.md                   the spec + Adam's §4 answers
  data/
    listings.json            latest sweep (the live record)
    notes.json               Adam's status + free-text, keyed by listing URL
    osm_amenities.geojson    cached walkability points (build_osm_cache.py)
    snapshots/               archived sweeps for change-detection + audit
  scripts/
    sweep.py                 orchestrate: score + diff + write + regenerate 07
    score.py                 Tier 1 + Tier 2 + Euclidean catchments (pure)
    zoning.py                NSW zoning URL builder + response parser
    render.py                regenerate 07-property-shortlist.md (Q7=b)
    build_osm_cache.py       fetch OSM amenities (run locally, monthly)
    serve.py                 local server for notes write-back
```

## Standing rules (from `../CLAUDE.md`)
- Any ad-hoc "today / inspection" question asked outside the dashboard still
  triggers a fresh live sweep — don't answer from cached `listings.json` alone.
- Verify zoning on the NSW Planning Portal for any warehouse-character listing.
- State provenance; flag prices as agents' guides needing re-verification.
- If the Chrome extension isn't connected, say so and ask to connect it.
