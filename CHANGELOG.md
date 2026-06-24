# Dashboard — Development Log

A record of the engineering work on the live-properties dashboard, written so a
fresh session (or Adam) can reconstruct what exists and why. `SCOPE.md` is the
original spec; `RUNBOOK.md` is the operating procedure; this file is the history
of how the code got to its current shape.

---

## 24 June 2026 — Bookmarklet auto-adds an unmatched listing as a new entry

The enrichment bookmarklet previously only updated a property already in `listings.json`;
running it on a page the dashboard didn't know about returned a 404. It now **creates** the
listing when there's no match, so a single click both discovers and files a property. (Decision
#30.)

- **Create-on-no-match** (`serve.py` `_handle_enrich_listing`): the old "no match → 404" branch
  now mints a new entry from the scraped data. It parses the price guide to numeric bounds
  (reusing `parse_alert_email.parse_price`), stamps `change_flag:"NEW"` / `first_seen` /
  `last_seen` / `source`, geocodes the new entry best-effort (so its transport/supplies/
  walkability catchments score on the same pass rather than waiting for the next sweep), then
  rides the **existing** re-score-all → rebuild-counts → regenerate-`07` → auto-push tail. This
  is the same shape and code path `gmail_fetch.merge_new_listings` and `/api/refresh` already
  use for email-ingested listings, so a bookmarklet-born entry is indistinguishable downstream.
  Listings stay keyed by URL; a later click on the same property matches by URL-id and enriches
  in place instead of duplicating.
- **Empty-shell guard**: an extraction with no usable identity — no address **and** no beds
  **and** no price number — is refused with HTTP **422** and nothing is stored, rather than
  inserting an un-scoreable blank (mirrors `sweep.is_empty_listing`). A price-less but identified
  listing (e.g. "Contact Agent" with beds + address) **is** added, with budget left an honest `?`.
- **Scope** (Adam's choice): **any** deliberately-clicked listing is added regardless of area or
  budget — the scorer still marks Tier 1 and target-area honestly — rather than gating creation
  on Inner West + ≤$2.2M, so a mis-parsed suburb can't silently drop a property Adam wanted.
- **Bookmarklet address hardening** (`enrich-bookmarklet.js`): the dead Domain URL-slug stub was
  replaced with a working parser used as a fallback after the page-heading parse. It pulls
  address, multiword suburb and **postcode** from the Domain slug (`…-suburb-nsw-postcode-id`),
  handling unit `5/40` and alpha `12a` forms. This matters specifically for new entries, which
  can't be geocoded or de-duplicated without an address. Scoped to Domain pages, so REA URLs are
  untouched.
- **Submit popup** (`enrich-submit.html`): now branches on `result.created` to show "**Added as
  a NEW listing**" vs "Matched an existing listing"; `bookmarklet.html` install/usage copy
  updated to describe the auto-add.
- **Data fix**: `data/listings.json` was found **truncated/corrupt** on disk (invalid JSON,
  mid-record at line 3770) — every `/api` handler `json.load`s it first, so the next enrich or
  refresh would have 500'd. Restored from the `2026-06-23T11-36Z` snapshot (68 listings, 64 Tier
  1 pass); the corrupt file is preserved as `data/listings.json.corrupt-20260624`.
- **Verified**: the create / enrich-existing / empty-shell-refused / price-less-but-identified
  branches and counts checked against the real `sweep` + `parse_alert_email` modules, and the
  slug parser against five Domain URLs via Node — all green. (Note: the Linux bash mount again
  served truncated/garbled copies of `serve.py` and `score.py` this session, so the literal
  server couldn't be run end-to-end in the sandbox; the host files are complete, and the
  unchanged scorer is exercised identically by the existing new-listing paths.)

### Follow-up (same day) — realestate.com.au support

Auto-add worked on Domain but **not on REA**: REA renders client-side and renames its CSS
classes, so the bookmarklet's class-based address selectors missed, leaving address/suburb empty,
and (unlike Domain) there was no URL fallback — so the server rejected the listing as
unidentifiable. Fixed with two **markup-independent** sources plus a guard relaxation:

- **Shared schema.org JSON-LD extractor** (`enrich-bookmarklet.js`, runs for both sites): walks
  every `ld+json` block (incl. `@graph`) and back-fills `address` (`streetAddress`), `suburb`
  (`addressLocality`), `postcode` (`postalCode`), `beds`/`baths` and — when present — `geo`
  lat/lon, *only* where the site-specific scrape left a gap. The geo coords let a brand-new
  listing be Tier-1 scored without the server geocoder.
- **REA URL parser** (`enrich-bookmarklet.js`): `/property-<type>-<state>-<suburb>-<id>` yields
  the suburb and property type reliably (handles multiword suburbs like `dulwich-hill`), so a REA
  listing always has at least a suburb + type identity even when JSON-LD and the DOM both miss.
- **Identity guard relaxed** (`serve.py` create branch): a deliberate click on a real listing URL
  (numeric id) **plus** a suburb or address is now sufficient to create, even when beds/price
  weren't read on the first click; genuine junk (no id, or no place) is still refused (422).
- **Verified**: the REA URL parser and JSON-LD walker against the reported URL
  (`…glebe-150693264`) and crafted JSON-LD (address/beds/baths/geo, `@graph` nesting, gap-fill,
  no-clobber) via Node — 6/6; and the relaxed guard against the real `sweep`/`parse_alert_email`
  (REA-thin accepted, junk refused) — 6/6. The live REA page itself couldn't be inspected — both
  the browser and fetch tools block realestate.com.au — so the fix keys off standards-based
  sources rather than REA's markup; **worth a quick confirm on a real REA listing.**

### Follow-up 2 (same day) — REA blocked the loader entirely (CSP); inline bookmarklet added

The extraction fix above was moot on REA because the bookmarklet never *ran* there: "nothing
happens" on click. Cause: the installed bookmarklet injects an external
`<script src="http://localhost:8777/enrich-bookmarklet.js">`, and **realestate.com.au serves a
strict Content-Security-Policy** whose `script-src` doesn't whitelist localhost, so the browser
refuses the script silently. (Domain's CSP is permissive, so the loader works there;
`http://localhost` is exempt from mixed-content blocking, which is why it loads at all.)

- **Inline bookmarklet** (`serve.py` new `GET /bookmarklet` route): serves a page whose draggable
  link carries the **entire** `enrich-bookmarklet.js` inside a `javascript:` URL (percent-encoded
  whole, so newlines survive as `%0A` and the `//` line comments stay terminated). An inlined
  bookmarklet loads no external script, so CSP `script-src` can't block it, and it runs
  synchronously inside the click gesture, so the result popup isn't popup-blocked either. The page
  is generated **from the file on disk on each request**, so it always reflects the latest code —
  re-drag the link after any change to `enrich-bookmarklet.js`. `bookmarklet.html` now banners a
  link to it for REA users; the old loader remains for reference.
- **Verified**: the encode→decode→execute round-trip (Python `quote(safe="")` → Node
  `decodeURIComponent` + eval, mirroring how a browser runs a `javascript:` URL) — the IIFE runs,
  comments don't eat code, and the `enrich-submit.html?data=` popup URL is built correctly.
- Adam: open <code>http://localhost:8777/bookmarklet</code>, drag the new button, and use it on
  REA. (The sandbox mount was serving a stale pre-edit copy of `enrich-bookmarklet.js`, so the
  inline link had to be built server-side from the real file rather than in the sandbox.)

### Follow-up 3 (same day) — REA beds/baths/parking + never-silent hardening

Inline bookmarklet now runs on REA, but beds/baths/parking came back empty: the REA branch read
them via `[class*="feature"]` selectors that don't match REA's current markup. Replaced with a
multi-strategy reader that doesn't depend on REA's CSS classes:

- **`enrich-bookmarklet.js`** REA beds/baths/parking/size: (1) REA's embedded data layer via regex
  on the page source — both the nested `"generalFeatures":{"bedrooms":{"value":N}…}` /
  `"propertySizes":{"building":{"displayValue":"N"}}` shape and a flat `"bedrooms":N` /
  `"parkingSpaces":N` / `"carspaces":N` shape; (2) element **aria-labels** (`"2 bedrooms"`,
  `"1 car space"` — REA's accessibility labels are stable); (3) the old class scan as a last
  resort. Each value is filled only if still missing, and bounded (0–20) to reject garbage.
- **Never-silent hardening** (added when REA gave "nothing"): the whole extraction is wrapped in
  try/catch and the submit popup now **always** opens — with a same-tab fallback if the popup is
  blocked, and a clear alert if the local server is unreachable. A failed selector on an
  unfamiliar layout can no longer abort the run with no feedback.
- **Verified**: the new block syntax-checked and run against simulated REA data — nested
  `generalFeatures`+`propertySizes`, flat keys, and aria-labels-only all yield beds 2 / baths 2 /
  parking 1 / 106 m². The live REA page still can't be inspected from here — the browser tools
  block realestate.com.au for both navigation *and* in-page reads — so the reader targets REA's
  standard data shapes; **confirm on the real listing** (the popup shows Beds/Baths/Parking).
- **Install de-duplication**: a recurring "nothing happens" turned out to be the *old loader*
  bookmark being clicked instead of the inline one (both were labelled "Enrich Listing"). The
  inline button is now labelled "⭐ Enrich Listing (INLINE — use this one)" with a red "delete your
  old bookmark first" warning, and `bookmarklet.html` no longer exposes a draggable loader — it
  only links to `/bookmarklet`. Confirmed via the live data that the inline bookmarklet does work:
  it had already created `1101/89 Bay Street, Glebe` (source realestate, flag NEW) — only the
  beds/baths/parking were missing, which is what the reader above fixes.

### Follow-up 4 (same day) — REA price (nested object, unlike Domain's flat field)

Price came back empty on REA. Cause: Domain leaks a **flat** numeric (`"exactPrice":1850000`),
but REA **nests** it — `"price":{"value":1850000,"display":"$1,850,000"}` / `"priceDetails":{...}`
— and/or exposes only a display string. REA's old regex `"price":\s*"?\$?([\d,]+)` matched a number
immediately after the key, so it never matched REA's `"price":{…}` and price stayed blank.

- **`enrich-bookmarklet.js`** REA price now mines, in order: (a) a numeric value inside a nested
  `price`/`priceDetails` object or a flat numeric field (`displayPrice`/`exactPrice`/`searchPrice`/…),
  flagged `(hidden guide)`; (a2) a **display string inside the nested price object**
  (`"$1.85m"`, `"Contact Agent"`); (b) a `$`-bearing display string under any `…price…` key;
  (c) a no-number guide string (Contact Agent / Auction / Offers / EOI / Guide); existing visible-DOM
  selectors; then (d) a new **meta/og-description** safety net that restates the guide. All numerics
  are range-guarded ($100k–$50M, 5–8 digits) so a stray strata/land figure can't slip in. The
  server's `parse_price` still turns `$1.85m`/ranges into bounds and the one-directional price merge
  keeps a real number over a "Contact Agent" placeholder.

### Follow-up 6 (same day) — reconciled to a single bookmarklet page

Two install pages had accumulated (`bookmarklet.html` the deprecated loader, `bookmarklet-inline.html`
the server-written inline copy), which was confusing. Reconciled to **one** canonical page,
`bookmarklet.html`, that always carries every upgrade:

- `serve.py` now writes the generated inline page to **`bookmarklet.html`** (was
  `bookmarklet-inline.html`) and serves the live page at `/bookmarklet`, `/bookmarklet.html`, and
  `/bookmarklet-inline.html` (old links still resolve). The page is generated from the current
  `enrich-bookmarklet.js`, so the one file is always up to date; it is rewritten on every visit.
- The two old files were renamed for reference only: `bookmarklet-loader.deprecated.html` and
  `bookmarklet-inline.deprecated.html`.
- A static seed `bookmarklet.html` was committed for the GitHub Pages / file:// case; it redirects to
  the live `/bookmarklet` when the local server is up, and `serve.py` overwrites it with the baked
  button on first visit.
- **Verified** on the running server: `/bookmarklet` and `/bookmarklet.html` both return the inline
  button (200); the regenerated `bookmarklet.html` (~55 KB) decodes to a 29,726-char script that
  parses clean and contains all fixes — REA nested-price (`numPats`), Domain Buyer's Guide,
  beds/baths reader (`grabNum`), and the always-open hardening.

### Follow-up 5 (same day) — Domain "Buyer's Guide" + abbreviated amounts

A Domain price written `Buyer's Guide $1.8m` wasn't picked up. The visible-price regex captured
`\$([\d,]+)`, which stops at the decimal — so `$1.8m` was read as "$1" and rejected by the
$100k floor — and the label list didn't include "Buyer's Guide". Rewrote the Domain visible-price
fallback (`enrich-bookmarklet.js`): an `AMT` token that matches `$1,800,000` / `$1.8m` /
`$1.8 million` / `$950k`; a label list covering Buyer's Guide / Price Guide / Guide / Offers
Over-Above-From / Asking / EOI / Expressions of Interest, allowing a short connector ("of"/":"/"-")
between label and amount; range support; and a `toNum` that resolves m/mil/million/k for the
sanity guard. A bare `$x – $y` range with no label is still caught; a single unlabelled `$x` is
deliberately not (avoids grabbing strata/median figures — the hidden-price miner handles genuine
flat prices). **Verified** against 14 cases in Node — Buyer's-Guide/abbreviated/range/million all
extract; a `$1,250 per quarter` strata line and an unrelated "buyer's guide to financing" sentence
both correctly yield nothing. (Couldn't confirm on the live page — Domain in-page exec was denied
this time — but the example `8 Park Street, Erskineville` matches the handled shapes.)
- **Verified**: the matcher against nine REA price shapes (nested value, nested `$`/Contact-Agent
  display, `priceDetails`, flat numeric, flat `$` string, range, auction text) — 8 extract correctly
  and a strata/land-price decoy correctly yields nothing; full file re-parsed clean via the live
  server. Live REA page still not inspectable from here — **confirm on the listing** (popup shows
  Price; or I can read it back from `listings.json`).

## 23 June 2026 (pm) — Hidden price persists "over the top of" a no-price auction guide

Auction / "Contact Agent" listings publish no price, but the agent's guide is usually
embedded in the page JSON. We already mined it; this change makes a mined hidden price
**stick** — a later price-less update (a repeat enrichment, or a re-ingested auction
alert email) can no longer revert it to "no price". Per the project's accuracy rule,
mined figures stay tagged `(hidden guide)` so they read as guides needing re-verification.

- **Bookmarklet** (`enrich-bookmarklet.js`): hidden-price mining broadened for both
  Domain and REA. Added Schema.org `offers.price` (JSON-LD, the most reliable structured
  source — present even on auction listings) ahead of the regex layer, plus a few more
  Domain embedded-JSON fields (`priceTo`, `displayPriceFrom`, `searchPrice`,
  `price.from`). All kept under the $100k–$50M guard and 6–8-digit bounds so a stray
  strata / land / sold figure can't slip through. Still flagged `(hidden guide)`.
- **Enrich merge** (`serve.py` `_handle_enrich_listing`): price merge is now
  **one-directional**. A discovered numeric price always overwrites the text and refreshes
  `price_min`/`price_max`; a no-number placeholder ("Auction…", "Contact Agent") is written
  **only** when no better price is on file, so it can never clobber a hidden guide we've
  already found.
- **Re-ingestion** (`sweep.py` `merge_incremental` + new `_preserve_enrichment`): when a
  saved-search alert re-lists an already-enriched property with no number, the prior
  record's price (and other email-absent enrichment — photo, beds/baths, description,
  features, direct URL) is carried onto the new record instead of being wiped. No false
  `PRICE_CHANGED` flag fires in that case; a genuinely different new number still wins and
  still flags.
- **Verified**: `_has_price_number`, `_preserve_enrichment`, `merge_incremental`, and the
  serve.py merge branch checked on crafted cases (hidden-over-auction, placeholder-can't-
  revert, genuine-new-number-wins, re-ingestion-preserves, `parse_price` on the
  `(hidden guide)` tag) — all green. (Note: the Linux bash mount again served a truncated
  `serve.py`; the host copy is complete — verification ran on the synced functions + a
  faithful replica of the edited branch.)

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

### Criteria changes & fixes (later same day — decision #28)

- **Bedrooms → ≥2 for all dwelling types** (was apartments ≥2 / cottages exactly 2). 3 still
  preferred for apartments; dwelling scale is a Tier 2 matter, not a bedroom ceiling.
- **Property type broadened** — freestanding houses/cottages/semis/terraces/townhouses are now
  admissible in their own right (the ≤2-bedroom cottage cap of #14 is lifted); raw shells still
  excluded. Amends decisions #5/#14.
- **Property-type matching bug fixed** — most listings carry Domain's compound label
  `"apartment / unit / flat"`, which the old *exact-equality* test didn't match, so property
  type read `?` across most of the board. Matching is now **token/substring-based**
  (`APT_TOKENS` / `HOUSE_TOKENS`); the lift requirement (accessibility) still keys off
  apartment-type buildings only, not houses.
- **"T1 Fail!" stamp** — cards whose `tier1.pass === false` now show a rotated red "T1 Fail!"
  stamp (top-right), via a `.t1stamp` style and a conditional in `card()`. Listings with only
  `?` items (no determinable fail) are *not* stamped.
- **Budget self-heals from price text** — `score.py` now back-fills numeric `price_min`/`price_max`
  from `price_guide_text` (new `price_bounds_from_text`) when the numbers are missing, so the
  budget mark resolves on *any* re-score. Fixes listings (e.g. `21/59 Wrights Road`) whose price
  text was set by an enrichment predating the price-parsing, leaving `price_min: null` and budget `?`.
- **Server resilience** — `_json` swallows client-disconnect errors (`ConnectionError`/`BrokenPipeError`)
  instead of logging a traceback (the slow `/api/push` was triggering `WinError 10053` when the
  browser hung up early); the Push button is now disabled while a push is in flight.
- **Accessibility auto-detection from description** — for apartment-type dwellings, a lift/elevator
  mentioned *in context* (`LIFT_CONTEXT_RE`) now scores a **provisional ✓** for step-free/lift, and an
  explicit "no lift" / "walk-up" (`LIFT_NEGATIVE_RE`) a **provisional ✗**. Context-aware: matches a
  building lift as a noun, not the verb ("lifts your lifestyle") nor "stairlift"/"facelift"/"uplifting".
  Each provisional verdict carries a `accessibility_basis` string shown in the drawer; a manual verdict
  overrides; gated to apartments (a lift is moot for a single-level house). Verified on positive,
  negative, and false-positive phrasings.

### Accessibility automation expanded (suggestions 1-6)

- **Unified `score._auto_accessibility`** — resolves step-free/lift from the structured **features
  list** (weighted first) and the description, after manual verdict + REA provenance: a `Lift`/`Elevator`
  feature chip or an explicit step-free/level-access phrase, a **ground/street-level** dwelling, an
  apartment lift-in-context, or a **house single-level / level-entry** → provisional ✓; "no lift"/"walk-up"
  or **stairs-to-entry / steep approach** → provisional ✗; colliding signals → left `?` (ambiguous).
  Manual verdict still wins (auto sits under `elif acc_val is None`). 11-case standalone test green.
- **Bookmarklet captures the Property features list + JSON-LD** (`data.features`, `data.floor`) from
  Domain/REA — structured chips ("Lift", "Intercom", …) are far more reliable than prose; `serve.py`
  merges them, and the drawer shows them (accessibility-relevant chips highlighted).
- **"Needs access check" filter** — toolbar checkbox surfacing listings whose step-free/lift is `?`
  or only provisional (not manually confirmed), as a verification worklist.
- **Flush empty placeholder listings** — `sweep.is_empty_listing` drops shells with no address AND no
  price AND no bedroom count (alert-parsing fragments that rendered as blank "Price n/a / ? / - bd" cards).
  Applied in the Refresh pipeline, the save-notes re-score, and the scheduled sweep, so they're removed
  and stay out (refresh reports `empties_removed`). The previous code deliberately *preserved* every
  address-less record; now address-less records are kept only if they still carry real data.
- **Description capture raised 500 → 2000 chars, plus a full-text lift/step-free safety net** — long
  descriptions put the "- … lift access …" bullet beyond the old 500-char cap (e.g. 16/162-166 Victoria
  Rd, where the lift was only in the bullets and not a structured feature chip). The bookmarklet now
  scans the *full* description for a building-lift / step-free phrase and records a synthetic
  `Lift (listed)` / `Step-free access (listed)` feature, so the signal survives truncation.
- **REA filter-provenance (suggestion 6)** — mechanism already wired; documented the enable steps in
  the RUNBOOK. The `accessibility_config.json` flag stays **off** until the filters are actually added
  to the saved search (turning it on early would assert false provisional passes).
- These need a re-score to show on existing listings: restart `serve.py` (to load the new
  `score.py`) then **Refresh now** (re-scores all). A plain F5 only reloads, it doesn't recompute.
- *Caveat:* the Cowork Linux mount served stale/truncated copies of the re-edited files this
  session, so the property-type changes couldn't be exercised end-to-end in the sandbox; the
  host files are complete and correct (confirmed by direct read), and earlier branches were
  verified when the mount was in sync. Worth a live smoke-test on the host.

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
