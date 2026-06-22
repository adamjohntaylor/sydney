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
   14 days, ISO), auction (ISO/null), description, lat, lon`.
   - `property_type` one of `apartment, house, townhouse, warehouse_conversion`;
     set `is_raw_shell: true` for unconverted shells (Q2 excludes them).
5. **Outlook** (Q4): set `outlook: {class, basis}`, class ∈ `water, park,
   elevated_district, city, leafy, none`, from description + cover image.
6. **Warehouse character**: set `warehouse_character`. If true, **zoning** (Q5):
   `scripts/zoning.py url <lat> <lon>` gives the ArcGIS URL; read its JSON; set
   `zoning` via `zoning.parse_zoning(...)`. E3/E4 ⇒ Tier 1 fail.
7. Write `dashboard/data/harvest-YYYYMMDD.json`:
   `{"generated_at_sydney": "...", "sweep_provenance": "...", "listings": [ ... ]}`.

### B. Score + merge + write (script)
```
python scripts\sweep.py data\harvest-YYYYMMDD.json --incremental
```
`--incremental` MERGES the new listings into the existing `data/listings.json`
(alert emails are new-only, so absence must not mark a listing withdrawn). It
computes catchments + Tier 1 + Tier 2, flags NEW / PRICE_CHANGED / OPEN_HOME_ADDED,
preserves Adam's `notes.json` status/notes by URL, writes `data/listings.json` +
a timestamped snapshot, and regenerates `../07-property-shortlist.md`.

*(Manual full-snapshot mode - drop `--incremental` - is retained for the case
where you ever supply a complete current field; it auto-detects WITHDRAWN/SOLD by
absence. Don't use it with new-only alert data.)*

## The criteria, as encoded (see `scripts/score.py`)

**Tier 1 (pass/fail; `None` = can't tell → flagged, never a silent fail):**
budget ≤ $2.2M · property type (apartment / ≤2BR cottage / warehouse-conversion;
raw shells excluded, Q2) · step-free + lift (usually unverifiable from a listing →
shown as `?`) · beds (apt ≥2, cottage 2) · transport ≤1.5km · daily supplies ≤1.5km ·
in target area · zoning E1/E2/MU1 for warehouse stock.

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
