# Live Properties Dashboard — Scope

*Status: BUILT 21 June 2026 (decision #26). Adam's §4 answers are applied. This
document is now the historical spec; current operating procedure is in `RUNBOOK.md`.*
*Drafted 20 June 2026.*

> **Addendum (21 June 2026, decision #27):** the §5.2/§5.3 listing source changed
> from scraping Domain to **email-alert ingestion (route A)** — Domain prohibits
> scraping and blocks automation, and its free API is sandbox-only. The sweep now
> reads Domain/REA saved-search alert emails via a Gmail/Outlook connector, then
> opens individual listing pages for detail; `sweep.py --incremental` merges the
> new-only alert data. The Tier 1/Tier 2 criteria below are unchanged.

This document is self-contained for a fresh session. It records the suitability criteria the dashboard is to encode (inferred from the project), the dashboard's proposed shape, the open ambiguities (with my leans as session-resume defaults), and the build plan. A new session can read this file, the standing files it references, and proceed to the build.

---

## 1. Project context (one paragraph)

The Sydney Relocation Project (folder root `D:\Projects\Sydney\`) supports Adam Taylor and Lee Harrison's planned 2027–28 move from Moonee Ponds to a Sydney Inner West dwelling. The project's standing files include the criteria document `02-location-and-property-criteria.md`, the decision log `05-decision-log.md`, the historical shortlist `07-property-shortlist.md`, and the folder-level `CLAUDE.md` which encodes the live-sweep standing rule. Anything below derives from those files and is intended to operationalise them. *See `README.md` for the wider documentation map.*

## 2. The dashboard's purpose

A persistent, browser-openable watchlist of every property currently on the Inner West market that satisfies (or comes close to satisfying) the project criteria, refreshed periodically, with Adam's annotations carried across refreshes. It replaces `07-property-shortlist.md` in its role as the live record; `07` becomes a frozen narrative of the 10 June 2026 market survey. The CLAUDE.md live-sweep standing rule continues to govern any single ad-hoc question asked outside the dashboard.

## 3. Inferred suitability criteria (the filter logic to encode)

Drawn from `02-location-and-property-criteria.md` and decisions #5, #9, #14, #15, #17 in `05-decision-log.md`.

### Tier 1 — hard parameters (deal-breakers; boolean pass/fail)

| Criterion | Encoded rule |
|---|---|
| Budget | Listing guide / asking ≤ **$2,200,000** (decision #9) |
| Property type | Apartment (primary), OR 2-bedroom brick or stone cottage (decision #14 — admissible alternative, not co-equal) |
| Accessibility | Step-free access; flat surrounding terrain; **lift required for apartments**; cottage must be single-level / level-entry |
| Bedrooms | Apartments: 3 preferred, 2 minimum; cottages: 2 |
| Public transport | Within 1,500 m of a train station, light-rail stop, or very well-serviced bus route |
| Daily supplies | Supermarket (Metro-format fine) or corner shop in or very near the dwelling |
| Location | Inner West target area: Zetland through Dulwich Hill plus Drummoyne north of Victoria Road (decision #15). Manly / Northern Beaches excluded (decision #6) |
| Zoning (warehouse-character only) | Must be E1 / E2 / MU1. E3 / E4 stock fails — residential is prohibited under IWLEP 2022 cl 6.13 (decision #17). NSW Planning Portal Spatial Viewer is the source of truth |

### Tier 2 — strong discriminators (heavily weighted; scored, not deal-breakers)

| Criterion | Weight | Source |
|---|---|---|
| **Outlook quality** — water > park > elevated district > leafy > city > none | Leading | Decision #17, elevated to leading 10 June 2026 |
| Living-area scale, target ≥ 115 m² internal | High | Decision #17 |
| Warehouse-conversion character (volume, high ceilings, exposed brick/steel, abundant light) | Medium | Decision #17 — but only where Tier 1 accessibility still passes (single-level, lifted buildings) |
| Light and aspect | Medium | `02` |
| Pool within 1,500 m (in district, not in building) | Medium | `02` |
| Parks within 1,500 m | Medium | `02` |
| Restaurants within 1,500 m | Medium | `02` |

### Tier 3 — explicitly deprioritised (ignore for ranking)

- Entertainment walkability (transit into the city suffices)
- In-building amenities (district facilities suffice)

### Implicit financial preference

- **Low strata fees.** ROA models strata at ~$12,000 p.a. initially, indexed. Candidates with high levies are marked down. Cottages substitute owner-borne maintenance and insurance and are not yet re-modelled (raise to adviser if a cottage becomes a live candidate — decision #14 open point).

## 4. Open ambiguities — Adam to resolve at session resume

My leans are recorded as session-resume defaults; a fresh session should treat each as the working answer unless Adam says otherwise.

| # | Question | Options | My lean (default) |
|---|---|---|---|
| 1 | Refresh model | (a) live sweep every open, (b) scheduled background sweep + cached snapshot + on-demand refresh button, (c) on-demand only | **(b) twice-weekly automatic** (Tuesday + Friday 06:30 Sydney) plus a manual Refresh-now link |
| 2 | Property-type scope | (i) Tier-1-compliant apartments + 2BR cottages only, (ii) (i) plus warehouse-conversion apartments meeting Tier 1, (iii) (ii) plus raw warehouse shells suitable for conversion | **(ii)** — shells make the dashboard noisier without being decisional given unassessed conversion cost |
| 3 | Walkability test | (a) trust agent narrative, (b) Euclidean 1,500 m from lat/lon to OSM-tagged amenities, (c) routed walking distance via OSRM / Mapbox | **(b) Euclidean MVP** for the watchlist; **(c) OSRM** as a follow-on for shortlist-stage detail only |
| 4 | Outlook scoring | (a) Claude auto-classifies from description + cover image at sweep time, (b) raw description surfaced, Adam scores manually, (c) hybrid: auto-classify with Adam override | **(c) hybrid** |
| 5 | Zoning auto-check | (a) auto-check every warehouse-flagged listing at sweep time and Tier 1 fail if E3/E4, (b) flag warehouse listings as "zoning unverified" for manual check | **(a) auto-check** — the rule is non-negotiable, the cost is small |
| 6 | Annotation persistence | sidecar JSON `dashboard/notes.json` keyed by listing URL, vs. some other store | **Sidecar JSON** |
| 7 | Role of `07-property-shortlist.md` | (a) dashboard supersedes `07` as the live record; `07` frozen as historical 10 June narrative, (b) dashboard regenerates `07` on each refresh | **(a)** — HTML and markdown serve different jobs |

Answers from Adam:
1: twice-weekly automatic
2: (i) plus warehouse-conversion apartments meeting Tier 1
3: Euclidean MVP** for the watchlist; **(c) OSRM** as a follow-on for shortlist-stage detail only
4. Claude auto-classifies from description + cover image at sweep time
5. Zoning auto-check | (a) auto-check every warehouse-flagged listing at sweep time and Tier 1 fail if E3/E4
6. Sidecar JSON
7. dashboard regenerates `07` on each refresh so that it can be manually written to Claude.au version of Sydney project for sharing with family.

Two further checks raised in scoping that Adam should also resolve:

- **Strata levy treatment** — soft Tier 2 penalty (current `02` framing) or a hard ceiling at e.g. > $14K p.a.? My lean: **soft Tier 2 penalty**, preserving `02`.
- **Pool catchment tier** — `02` puts the 1,500 m pool within Tier 2; an alternative reading would lift it to Tier 1. My lean: **keep at Tier 2 per `02`**.

## 5. Proposed shape (build-ready spec)

### 5.1 Hosting and layout

All artefacts under `D:\Projects\Sydney\dashboard\`:

```
dashboard/
├── SCOPE.md                       # this file
├── index.html                     # the dashboard itself (self-contained, opens in a browser)
├── data/
│   ├── listings.json              # the latest sweep
│   ├── notes.json                 # Adam's annotations (status + free-text), keyed by listing URL
│   ├── osm_amenities.geojson      # cached OSM amenities (transport stops, supermarkets, pools, parks, restaurants)
│   └── snapshots/
│       └── 2026-06-20T18-30Z.json # archived sweeps, for change-detection and audit
└── scripts/
    ├── sweep.py                   # orchestrates a refresh (calls Claude in Chrome for Domain queries; geocodes; scores)
    ├── score.py                   # Tier 1 boolean + Tier 2 weighted score
    ├── zoning.py                  # NSW Planning Portal lookup for warehouse-character listings
    └── render.py                  # injects listings.json into the HTML template (or HTML reads it client-side)
```

A single self-contained `index.html` with Tailwind via CDN and client-side JS for filtering / sorting. The dashboard reads `data/listings.json` and `data/notes.json` at load. Notes are written back by an inline `download` link (the browser cannot write to disk directly without a local server — see open question 3 below in the Build notes).

### 5.2 Data sources

| Source | Role | Access |
|---|---|---|
| **Domain** | Primary listings | Via the Claude-in-Chrome MCP, querying suburb clusters at ≤ $2.2M. The 10 June survey established Domain catches the relevant set |
| **realestate.com.au** | Supplement (gap-filling) | Via Claude in Chrome; REA's anti-bot posture makes it less reliable, so it's a check rather than a peer |
| **NSW Planning Portal — Spatial Viewer** | Zoning verification for warehouse-flagged listings | Query at sweep time per question 5 lean |
| **OpenStreetMap (Overpass API or pre-cached extract)** | Amenities (transport / supermarket / pool / park / restaurant) for walkability tests | One-time cache for the target area refreshed monthly |
| **Named-agent sites** (BresicWhitney, Knight Frank, The Agency, Adrian William, etc.) | Shortlist-stage detail only (full descriptions, floor plans, strata disclosure) | Manual / on-demand |

### 5.3 Sweep pipeline (one refresh)

1. Run Domain searches per suburb cluster covering the target area (Inner West core: Newtown, Camperdown, Glebe, Annandale, Leichhardt, Lilyfield, Rozelle, Balmain, Birchgrove, Marrickville, Dulwich Hill, Petersham, Stanmore, Enmore, Erskineville, Alexandria, Zetland; plus Drummoyne north of Victoria Road), filter to apartments + houses ≤ $2.2M.
2. For each listing, extract: address, suburb, listing URL, price guide / quoted range, bedrooms, bathrooms, parking, internal m² (if given), strata levy (if given), agent name + agency, cover image URL, all open-home times for the next 14 days, auction date (if any), description text.
3. Geocode address (OSM Nominatim or cached).
4. Compute Euclidean 1,500 m hit/miss for each catchment criterion against `osm_amenities.geojson`.
5. Outlook auto-classify (Claude as model) from description + cover image → one of {water, park, elevated_district, city, leafy, none} plus a one-sentence basis.
6. Warehouse-character heuristic from description tokens (warehouse, conversion, loft, exposed brick, sawtooth, etc.); if hit, query NSW Planning Portal Spatial Viewer → mark Tier 1 fail if E3/E4.
7. Tier 1 → boolean array of seven criteria + zoning where applicable; Tier 2 → weighted 0–100 score (outlook leading).
8. Diff against most-recent prior snapshot → assign change flag {NEW, PRICE_CHANGED, OPEN_HOME_ADDED, WITHDRAWN, SOLD}.
9. Carry forward Adam's annotations from `notes.json` by URL.
10. Write `data/listings.json` and a timestamped snapshot under `data/snapshots/`.

### 5.4 Fields in the dashboard (per-listing card)

Address + suburb; price guide; beds / baths / parking; internal m²; strata levy; agent + agency; **next open-home (day-of-week + time)**; auction date if any; outlook class with the one-sentence basis; Tier 1 ticks/crosses with reason for any fail; Tier 2 score with the leading factor named; zoning verdict (for warehouse stock); walkability catchment ticks (transport / supermarket / pool / park / restaurants); listing URL (open in browser tab); cover image; change flag; Adam's status pill (one of: needs decision, interested, inspected, shortlist, rejected, sold-without-us); free-text notes.

### 5.5 Interaction

- Header strip: last-refreshed timestamp + "Refresh now" link + count of NEW / PRICE_CHANGED / SOLD since last view
- Filter bar: suburb cluster, price band, beds, Tier 1-pass-only toggle, status, change-flag
- Sort: Tier 2 score (default), next open-home, price, change flag, time-on-market
- Tabs / views:
  - **All candidates** (default)
  - **This Saturday's inspections** — Tier 1 passes with an open-home this Saturday
  - **Auction calendar** — chronological by auction date
  - **Withdrawn / sold** — historical audit
- Per-card click → detail drawer: full description, walkability map (with the 1,500 m ring and OSM amenities), zoning result page, edit-notes form

### 5.6 Refresh mechanism (assuming lean on Q1: option b)

A scheduled task created via the `mcp__scheduled-tasks__create_scheduled_task` tool, running at 06:30 Sydney local on Tuesdays and Fridays, executing the sweep pipeline against the current criteria, writing `listings.json` and a snapshot, and pinging Adam on completion. Manual "Refresh now" link in the dashboard fires the same pipeline ad-hoc. The dashboard always reads the latest `listings.json` and displays its timestamp.

## 6. Standing rules to obey

From the folder-level `CLAUDE.md`:

1. Any *ad-hoc* "today / inspection" question, asked outside the dashboard, still triggers a live sweep — do not answer from the cached `listings.json` alone.
2. Verify zoning on the NSW Planning Portal Spatial Viewer for any warehouse-character listing.
3. State provenance for any recommendation — cite the listings used and flag prices as agents' guides requiring re-verification before any action.
4. When the Chrome extension is not connected, say so explicitly and ask Adam to connect it.

## 7. Build plan (next-session actions, in order)

1. **Get Adam's resolution on the seven ambiguities of §4** (and the two further checks). The leans recorded here are defaults if Adam doesn't override.
2. **Create `dashboard/data/osm_amenities.geojson`** by one-time Overpass query for the target-area bounding box.
3. **Build `scripts/sweep.py`** — Domain search via Claude in Chrome (per suburb cluster); listing parse; geocode; catchment compute; outlook classify; zoning check; score; diff; write.
4. **Build `index.html`** — Tailwind, vanilla JS, reads `listings.json` + `notes.json`, renders cards, filter/sort/tabs.
5. **Wire up the scheduled task** at 06:30 Sydney Tuesdays and Fridays.
6. **Initial sweep** — run `sweep.py` once manually to populate `listings.json`.
7. **Smoke-test** the dashboard in a browser; verify all Tier 1 / Tier 2 fields render and the filter / sort / status update work.
8. **Freeze `07-property-shortlist.md`** with a header pointer to the dashboard as the live record (per Q7 lean).
9. **Append a decision-log entry** recording the dashboard's adoption and the resolved answers to §4.

## 8. Known issues and open build questions

- **Notes write-back from a static HTML page.** A browser cannot write to disk without a local server. Three reasonable resolutions: (a) the dashboard exports notes as a downloadable JSON the user saves over `notes.json`, (b) a small local server (Python `http.server` + a tiny POST handler) hosts the dashboard, (c) a static HTML form generates a `notes.json` patch that Claude applies in the next session. **My lean: (b) local server, run via `python scripts/serve.py` in the dashboard folder.** Flag for Adam.
- **Domain anti-bot.** Claude in Chrome should be reliable for normal queries, but periodic captchas are possible. The sweep needs to surface "captcha-blocked" cleanly rather than fail silently.
- **OSM Nominatim rate limit.** One geocode per second; batch the per-listing geocoding accordingly.
- **NSW Planning Portal latency.** Adds 1–2 s per warehouse-flagged listing; tolerable given warehouse stock is a small fraction.

## 9. Sources for the criteria

- `D:\Projects\Sydney\CLAUDE.md` — standing live-sweep rule, zoning verification rule
- `D:\Projects\Sydney\02-location-and-property-criteria.md` — Tier 1 / Tier 2 / Tier 3 criteria
- `D:\Projects\Sydney\05-decision-log.md` — decisions #5 (property type), #6 (Manly excluded), #9 (budget), #14 (cottage alternative), #15 (Drummoyne extension), #17 (Tier 2 extension: outlook, living-area scale, warehouse-conversion character)
- `D:\Projects\Sydney\07-property-shortlist.md` — historical 10 June 2026 narrative, including the warehouse-conversion / view-led shortlist and the E3/E4 zoning trap
- `auto memory` — `warehouse-home-brief.md` (warehouse-character evolution and Inner West market facts as at 10 June 2026)

---

*Last updated 20 June 2026. To pick this up: re-read §1 and §3, read Adam's resolutions on §4 written directly under the *Question* section as "Answers from Adam:", then proceed to §7.*
