// content.js - runs on every domain.com.au / realestate.com.au page you open.
// It reads what the page itself says about the property's market status and
// hands a compact "status check" to background.js, which decides whether the
// dashboard should hear about it (always for pages the Verify run opened;
// otherwise only when the page reports a departure - sold / under offer /
// gone - so ordinary browsing never spams the dashboard).
//
// Vocabulary matches scripts/sweep.py: sold | under_offer | withdrawn |
// not_found | on_market | unknown, plus the two search-page kinds
// (search_count / sold_search) that background.js resolves itself.

function readPage() {
  const url = location.href;
  const host = location.hostname;
  const title = (document.title || "").trim();
  const text = () => (document.body ? (document.body.innerText || document.body.textContent) : "") || "";

  const listingId = (u) => {
    const m = (u || "").match(/(?:-|\/)(\d{6,12})\/?(?:\?.*)?$/);
    return m ? m[1] : null;
  };
  const latestDate = (s) => {
    const months = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };
    let best = null;
    for (const m of s.matchAll(/\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b/gi)) {
      const d = new Date(Date.UTC(+m[3], months[m[2].slice(0, 3).toLowerCase()] - 1, +m[1]));
      if (!isNaN(d) && (!best || d > best)) best = d;
    }
    return best ? best.toISOString().slice(0, 10) : null;
  };
  // Short, standalone badge elements only (not paragraphs that merely mention the words).
  const hasBadge = (re) => {
    for (const el of document.querySelectorAll("span,div,p,li,strong,b,h1,h2,h3")) {
      const t = (el.innerText || el.textContent || "").trim();
      if (t.length <= 45 && re.test(t)) return t;
    }
    return null;
  };

  let check = { url, final_url: url, kind: "listing" };
  const body = text();

  if (host.endsWith("domain.com.au") && /\/sold-listings\//.test(url)) {
    check.kind = "sold_search";
    const m = title.match(/^(\d[\d,]*)\s+Propert(?:y|ies)\s+Sold/i);
    check.count = m ? parseInt(m[1].replace(/,/g, ""), 10) : 0;
    check.sold_date = latestDate(body);
    const a = document.querySelector('a[href*="domain.com.au/"][href*="-20"]');
    if (a) check.resolved_url = a.href.split("?")[0];
    check.status_basis = check.count ? `Domain sold results list the address${check.sold_date ? ", sold " + check.sold_date : ""}`
                                     : "not in Domain sold results";
  } else if (host.endsWith("domain.com.au") && /\/sale\/\?|street=|\/results/.test(url)) {
    // Single-address for-sale search: the result count is the market status.
    check.kind = "search_count";
    const m = title.match(/^(\d[\d,]*)\s+Real\s+Estate\s+Propert/i);
    if (m) check.count = parseInt(m[1].replace(/,/g, ""), 10);
    else if (/^Real\s+Estate\s+Propert/i.test(title) || /No exact matches/i.test(body)) check.count = 0;
    else check.count = null;
    const a = document.querySelector('a[href*="domain.com.au/"][href*="-20"]');
    if (a) check.resolved_url = a.href.split("?")[0];
    check.status_basis = m ? `${check.count} for-sale result(s) for the address` :
      (check.count === 0 ? "address no longer in Domain for-sale results" : "search page without a result count");
  } else {
    // An individual listing page (Domain or REA).
    const lid = listingId(url);
    let status = "unknown", basis = "no recognisable status signal";
    const ld = [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => s.textContent).join("\n");
    if (/Page not found|404/i.test(title) || /couldn.t find (that|the) (page|property)|page not found/i.test(body.slice(0, 3000))) {
      status = "not_found"; basis = "page not found";
    } else if (/no longer available|listing (has been )?removed|this listing has expired/i.test(body.slice(0, 4000))) {
      status = "withdrawn"; basis = "page says no longer available";
    } else if (host.endsWith("realestate.com.au") && /\/sold\//.test(url) && lid) {
      status = "sold"; basis = "URL under /sold/";
    } else if (/^Sold\s+\S.*\bon\s+\d{1,2}\s+\w{3,9}\s+\d{4}/i.test(title)) {
      status = "sold"; basis = "page title 'Sold ... on <date>'";
      const st = body.match(/Sold\s+(?:by\s+private\s+treaty|at\s+auction|prior\s+to\s+auction)\b[^\n]{0,30}/i);
      if (st) basis += "; " + st[0].trim();
      check.sold_date = latestDate((st ? st[0] : "") + " " + title);
    } else if (/"availability"\s*:\s*"(https?:\/\/schema\.org\/)?SoldOut"/i.test(ld)) {
      status = "sold"; basis = "JSON-LD availability SoldOut";
    } else if (hasBadge(/^(under offer|under contract|deposit taken|contract exchanged)$/i)) {
      status = "under_offer"; basis = hasBadge(/^(under offer|under contract|deposit taken|contract exchanged)$/i);
    } else if (/"availability"\s*:\s*"(https?:\/\/schema\.org\/)?InStock"/i.test(ld) || body.length > 2000) {
      status = "on_market"; basis = "listing page rendered";
      const p = body.match(/\$\s?\d{1,3}(?:,\d{3}){1,2}|\$\s?\d(?:\.\d+)?\s?[mM]\b/);
      if (p) check.price_guide_text = p[0];
    }
    check.listing_status = status;
    check.status_basis = basis;
    // Listing facts (floor area first - Tier 1 needs internal_m2 >= 100 m²).
    // Read on every rendered listing page, not only on-market ones, so a sold
    // comparable still records its size. The dashboard merges them via
    // sweep.apply_check_details and re-scores.
    if (status !== "not_found" && body.length > 500) {
      try {
        const facts = readFactsFromPage();
        Object.assign(check, facts, readAreaFromPage(facts.property_type));
      } catch (e) { check.area_basis = "reader error: " + e.message; }
    }
  }
  check.status_basis = "chrome: " + (check.status_basis || "");
  check.page_title = title;
  return check;
}

// ---- Floor-area + facts reader (identical copy in enrich-bookmarklet.js) ----
// Reads the property's INTERNAL (floor/building) area and LAND area off the
// listing page, keeping the two apart: Tier 1 needs internal_m2 >= 100 and a
// land figure must never masquerade as it. Sources, most reliable first:
//   1. the site's embedded data layer. Domain (verified live 4 Oct 2026 on
//      1001/2 Cowper St Glebe): an analytics "property":{...} object near the
//      top carries "buildingsize":149 and "internalArea":149, with the listing
//      id a few hundred chars before it; the id-keyed listing block further down
//      holds "landSize":N (0 = not stated) and no internal figure; the page also
//      embeds OTHER listings' blocks ("similar properties"), so a match counts
//      only when this listing's id sits within the preceding 2,000 chars. REA:
//      "buildingSize"/"landSize" {value} objects, same proximity rule.
//   2. JSON-LD floorSize / lotSize;
//   3. visible text with an explicit label ("Internal 111m²", "450m² land") or
//      Domain's FAQ line "The internal land size for <addr> is 149m²";
//   4. an unlabelled "NNNm²" feature chip: an apartment has no land, so it is
//      internal; for a house it is taken as land and never as internal.
// `area_basis` records which source answered; `area_checked` says we looked.
function readAreaFromPage(ptypeHint) {
  const out = { area_checked: true };
  const whole = document.documentElement.innerHTML;
  const lid = (location.href.match(/(?:-|\/)(\d{6,12})\/?(?:\?.*)?$/) || [])[1] || null;
  const num = (v) => { const n = parseInt(String(v).replace(/[^\d]/g, ''), 10); return Number.isFinite(n) ? n : null; };
  const okInt = (n) => n != null && n >= 20 && n <= 2000;
  const okLand = (n) => n != null && n >= 30 && n <= 200000;
  // First match that belongs to THIS listing: the id must appear in the 2,000
  // chars before it (no id known -> first match anywhere).
  const grabNear = (res, ok) => {
    for (const re of res) {
      const g = new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g');
      let m;
      while ((m = g.exec(whole)) !== null) {
        const n = num(m[1]);
        if (!ok(n)) continue;
        if (!lid || whole.slice(Math.max(0, m.index - 2000), m.index).includes(lid)) return n;
      }
    }
    return null;
  };
  const INT_RES = [
    /"(?:buildingsize|buildingSize|buildingArea|floorArea|internalArea|internalSize|floorSize|livingArea)"\s*:\s*"?(\d{2,5})(?:\.\d+)?"?\s*[,}]/i,
    /"(?:buildingSize|buildingArea|floorArea|internalArea|floorSize)"\s*:\s*\{[^{}]*?"(?:displayValue|value)"\s*:\s*"?(\d{2,5})/i,
    /"propertySizes"\s*:\s*\{\s*"building"\s*:\s*\{[^{}]*?"(?:displayValue|value)"\s*:\s*"?(\d{2,5})/i
  ];
  const LAND_RES = [
    /"(?:landAreaSqm|landSize|landsize|landArea)"\s*:\s*"?([1-9]\d{1,5})(?:\.\d+)?"?\s*[,}]/i,
    /"(?:landSize|landArea|lotSize)"\s*:\s*\{[^{}]*?"(?:displayValue|value)"\s*:\s*"?([1-9]\d{1,5})/i,
    /"propertySizes"\s*:\s*\{[^]*?"land"\s*:\s*\{[^{}]*?"(?:displayValue|value)"\s*:\s*"?([1-9]\d{1,5})/i
  ];
  const basis = [];
  let internal = grabNear(INT_RES, okInt);
  let land = grabNear(LAND_RES, okLand);
  if (internal) basis.push('page data: building/floor area');
  if (land) basis.push('page data: land area');
  // JSON-LD (unitCode MTK = square metres)
  if (!internal || !land) {
    const ld = [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => s.textContent).join('\n');
    if (!internal) { const m = ld.match(/"floorSize"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d{2,5})/i); if (m && okInt(num(m[1]))) { internal = num(m[1]); basis.push('JSON-LD floorSize'); } }
    if (!land) { const m = ld.match(/"lotSize"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d{2,6})/i); if (m && okLand(num(m[1]))) { land = num(m[1]); basis.push('JSON-LD lotSize'); } }
  }
  // Visible text with an explicit label, either order ("Internal: 111m²", "111m² internal").
  const text = (document.body && (document.body.innerText || document.body.textContent)) || '';
  const M2 = '(?:m²|m2|sqm|sq\\.?\\s?m\\b|square\\s+met(?:re|er)s?)';
  const INT_WORDS = '(?:internal|interior|floor|living|building|total)(?:\\s+(?:floor\\s+)?(?:area|size|space))?';
  const LAND_WORDS = '(?:land|lot|block|site|allotment)(?:\\s+(?:area|size))?';
  const labelled = (words, ok) => {
    const a = text.match(new RegExp('\\b' + words + '\\s*[:\\-–]?\\s*(?:(?:of\\s+)?approx(?:imately|\\.)?\\s*)?(\\d{2,6})\\s*' + M2, 'i'));
    if (a && ok(num(a[1]))) return num(a[1]);
    const b = text.match(new RegExp('(\\d{2,6})\\s*' + M2 + '\\s*(?:\\(?approx\\.?\\)?\\s*)?(?:of\\s+)?' + words + '\\b', 'i'));
    if (b && ok(num(b[1]))) return num(b[1]);
    return null;
  };
  // Domain's generated FAQ: "The internal land size for <addr> is 149m²." (apartments)
  // / "The land size for <addr> is 450m²." (houses).
  if (!internal) { const m = text.match(new RegExp('internal(?:\\s+land)?\\s+size\\s+for\\s+[^\\n]{3,120}?\\s+is\\s+(\\d{2,5})\\s*' + M2, 'i')); if (m && okInt(num(m[1]))) { internal = num(m[1]); basis.push('page text: "internal size for ... is"'); } }
  if (!land) { const m = text.match(new RegExp('(?:^|[^l]\\s)land\\s+size\\s+for\\s+[^\\n]{3,120}?\\s+is\\s+(\\d{2,6})\\s*' + M2, 'i')); if (m && okLand(num(m[1])) && !/internal\s+land\s+size\s+for/i.test(text)) { land = num(m[1]); basis.push('page text: "land size for ... is"'); } }
  if (!internal) { const n = labelled(INT_WORDS, okInt); if (n) { internal = n; basis.push('page text: labelled internal/floor area'); } }
  if (!land) { const n = labelled(LAND_WORDS, okLand); if (n) { land = n; basis.push('page text: labelled land area'); } }
  // Unlabelled feature chip.
  if (!internal) {
    const pt = (ptypeHint || '').toLowerCase();
    const isApt = /apartment|unit|flat|studio|penthouse/.test(pt);
    const chip = text.match(new RegExp('(?:^|\\n|\\s)(\\d{2,4})\\s*' + M2 + '(?=\\s|$)', 'i'));
    const n = chip ? num(chip[1]) : null;
    if (n && okInt(n)) {
      if (isApt && n !== land) { internal = n; basis.push('page text: unlabelled m² figure (apartment ⇒ internal)'); }
      else if (!isApt && !land && okLand(n)) { land = n; basis.push('page text: unlabelled m² figure (house ⇒ land, not internal)'); }
    }
  }
  if (internal) out.internal_m2 = internal;
  if (land) out.land_m2 = land;
  out.area_basis = basis.length ? basis.join('; ') : 'no area figure found on the page';
  return out;
}

// Beds / baths / parking / property type for a listing page that the watchlist
// only knows as an alert-derived search URL (108 of 123 active records on
// 4 Oct 2026 had no beds or type). JSON-LD first, then the embedded data layer,
// then the accessible feature chips.
function readFactsFromPage() {
  const f = {};
  const src = document.documentElement.innerHTML;
  const lid = (location.href.match(/(?:-|\/)(\d{6,12})\/?(?:\?.*)?$/) || [])[1] || null;
  // REA's URL slug names the type precisely (property-apartment-nsw-..., property-
  // house-nsw-..., property-townhouse-...); its JSON-LD only says "Residence".
  { const m = location.pathname.match(/\/property-([a-z]+(?:-[a-z]+)?)-(?:nsw|vic|qld|sa|wa|tas|act|nt)-/i); if (m) f.property_type = m[1].toLowerCase().replace(/-/g, ' '); }
  // Same proximity rule as the area reader: the page embeds other listings'
  // data too, so a data-layer value counts only with this listing's id nearby.
  const grab = (res, lo, hi) => {
    for (const re of res) {
      const g = new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g');
      let m;
      while ((m = g.exec(src)) !== null) {
        const n = parseInt(m[1], 10);
        if (!(n >= lo && n <= hi)) continue;
        if (!lid || src.slice(Math.max(0, m.index - 2000), m.index).includes(lid)) return n;
      }
    }
    return null;
  };
  const grabStr = (res) => {
    for (const re of res) {
      const g = new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g');
      let m;
      while ((m = g.exec(src)) !== null) {
        if (!lid || src.slice(Math.max(0, m.index - 2000), m.index).includes(lid)) return m[1];
      }
    }
    return null;
  };
  document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
    try {
      const push = (o) => {
        if (!o || typeof o !== 'object') return;
        if (f.beds == null && o.numberOfBedrooms != null) { const n = parseInt(o.numberOfBedrooms, 10); if (!isNaN(n)) f.beds = n; }
        if (f.baths == null) { const r = o.numberOfBathroomsTotal != null ? o.numberOfBathroomsTotal : o.numberOfBathrooms; if (r != null) { const n = parseInt(r, 10); if (!isNaN(n)) f.baths = n; } }
        if (!f.property_type && typeof o['@type'] === 'string' && /^(Apartment|House|SingleFamilyResidence)$/i.test(o['@type'])) f.property_type = /house|residence/i.test(o['@type']) ? 'house' : 'apartment';
        if (Array.isArray(o['@graph'])) o['@graph'].forEach(push);
      };
      const j = JSON.parse(s.textContent); (Array.isArray(j) ? j : [j]).forEach(push);
    } catch (e) {}
  });
  if (f.beds == null) f.beds = grab([/"bedrooms"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d+)/i, /"bedrooms"\s*:\s*"?(\d+)"?/i, /"beds"\s*:\s*"?(\d+)"?/i], 0, 20);
  if (f.baths == null) f.baths = grab([/"bathrooms"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d+)/i, /"bathrooms"\s*:\s*"?(\d+)"?/i, /"baths"\s*:\s*"?(\d+)"?/i], 0, 20);
  if (f.parking == null) f.parking = grab([/"parkingSpaces"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d+)/i, /"parkingSpaces"\s*:\s*"?(\d+)"?/i, /"carspaces"\s*:\s*"?(\d+)"?/i, /"carSpaces"\s*:\s*"?(\d+)"?/i], 0, 20);
  const typeEl = document.querySelector('[data-testid="listing-summary-property-type"], [class*="property-type"]');
  if (typeEl && typeEl.textContent.trim().length < 40) f.property_type = typeEl.textContent.trim().toLowerCase();
  if (!f.property_type) { const v = grabStr([/"propertyTypeFormatted"\s*:\s*"([^"]{2,40})"/, /"propertyType"\s*:\s*"([a-z ]{2,30})"/i]); if (v) f.property_type = v.toLowerCase(); }
  if (f.beds == null || f.baths == null || f.parking == null) {
    document.querySelectorAll('[aria-label],[title]').forEach(el => {
      const l = (el.getAttribute('aria-label') || el.getAttribute('title') || '').toLowerCase();
      if (l.length > 40) return;
      const m = l.match(/(\d+)/); if (!m) return;
      const n = parseInt(m[1], 10); if (n < 0 || n > 20) return;
      if (f.beds == null && /\bbed(?:room)?s?\b/.test(l)) f.beds = n;
      else if (f.baths == null && /\bbath(?:room)?s?\b/.test(l)) f.baths = n;
      else if (f.parking == null && /\b(?:car|parking|garage)\b/.test(l)) f.parking = n;
    });
  }
  Object.keys(f).forEach(k => { if (f[k] == null) delete f[k]; });
  return f;
}

// Client-rendered pages (REA especially) can be nearly empty at document_idle:
// re-read a couple of times before reporting 'unknown' or a null count.
(function () {
  const inconclusive = (c) => (c.kind === "listing" && c.listing_status === "unknown")
                           || (c.kind === "listing" && c.listing_status === "on_market" && !c.internal_m2 && !c.land_m2)
                           || (c.kind === "search_count" && c.count === null);
  const attempts = [0, 2500, 5000];
  let i = 0;
  const go = () => {
    const c = readPage();
    if (inconclusive(c) && ++i < attempts.length) { setTimeout(go, attempts[i]); return; }
    try { chrome.runtime.sendMessage({ type: "status-check", check: c }); } catch (e) { /* extension reloaded */ }
  };
  go();
})();
