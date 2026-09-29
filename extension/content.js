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

(function () {
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
  }
  check.status_basis = "chrome: " + (check.status_basis || "");
  check.page_title = title;
  try { chrome.runtime.sendMessage({ type: "status-check", check }); } catch (e) { /* extension reloaded */ }
})();
