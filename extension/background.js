// background.js - the Verify run.
//
//   popup "Verify" -> GET localhost:8777/api/status-worklist?cap=N
//                  -> for each entry: open the URL in a background tab, wait for
//                     content.js to report, (for a gone search: also open the
//                     sold-listings search), close the tab
//                  -> POST localhost:8777/api/apply-status {checks:[...]}
//
// Outside a Verify run, a page you browse yourself is reported to the
// dashboard only when it says the property has departed (sold / under offer /
// gone), so ordinary browsing never rewrites listings.json.

const DASH = "http://localhost:8777";
const PAGE_TIMEOUT_MS = 25000;
const DELAY_MS = 1500;                // between pages - be a polite visitor
const SOLD_DATE_GRACE_DAYS = 60;      // older sale than first_seen-60d => historical

const state = { running: false, total: 0, done: 0, log: [], result: null, error: null };
const waiting = new Map();            // tabId -> resolve(check)
const verifyTabs = new Set();

function setState(patch) {
  Object.assign(state, patch);
  chrome.storage.session.set({ verifyState: state }).catch(() => {});
}
function log(line) { state.log.push(line); if (state.log.length > 400) state.log.shift(); setState({}); }
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg?.type !== "status-check" || !sender.tab) return;
  const tabId = sender.tab.id;
  const resolve = waiting.get(tabId);
  if (resolve) { waiting.delete(tabId); resolve(msg.check); return; }
  if (verifyTabs.has(tabId)) return;
  // Ad-hoc browsing: only departures are worth telling the dashboard about.
  const c = msg.check;
  if (c.kind === "listing" && ["sold", "under_offer", "withdrawn", "not_found"].includes(c.listing_status)) {
    postChecks([stripCheck(c)]).then(r => log(`browsed: ${c.listing_status} ${c.url} -> ${r.changed ?? "?"} change(s)`))
                             .catch(e => log("post failed: " + e.message));
  }
});

function stripCheck(c) {
  const out = { url: c.url, listing_status: c.listing_status, status_basis: c.status_basis };
  if (c.final_url && c.final_url !== c.url) out.final_url = c.final_url;
  for (const k of ["price_guide_text", "resolved_url", "sold_date", "address", "suburb"]) if (c[k]) out[k] = c[k];
  return out;
}

function openAndRead(url) {
  return new Promise(async (resolve) => {
    let tab;
    try { tab = await chrome.tabs.create({ url, active: false }); }
    catch (e) { return resolve({ url, kind: "listing", listing_status: "error", status_basis: "chrome: " + e.message }); }
    verifyTabs.add(tab.id);
    const timer = setTimeout(() => {
      waiting.delete(tab.id);
      finish({ url, kind: "listing", listing_status: "unknown", status_basis: "chrome: page gave no report within timeout" });
    }, PAGE_TIMEOUT_MS);
    const finish = async (check) => {
      clearTimeout(timer);
      verifyTabs.delete(tab.id);
      try { await chrome.tabs.remove(tab.id); } catch (e) { /* already closed */ }
      resolve(check);
    };
    waiting.set(tab.id, finish);
  });
}

function soldSearchUrl(searchUrl) {
  try {
    const u = new URL(searchUrl);
    const street = u.searchParams.get("street");
    if (!street) return null;
    return "https://www.domain.com.au/sold-listings/?" + new URLSearchParams({ street }).toString();
  } catch (e) { return null; }
}

const listingId = (u) => { const m = (u || "").match(/(?:-|\/)(\d{6,12})\/?(?:\?.*)?$/); return m ? m[1] : null; };

async function verifyEntry(w) {
  const c = await openAndRead(w.url);
  c.address = w.address; c.suburb = w.suburb; c.url = w.url;
  // A listing page that ended up somewhere else (another id, a suburb or
  // search page): the original listing is gone. REA's /sold/ move keeps the
  // id and content.js already reads it as sold.
  const id = listingId(w.url);
  const gone = (c.kind === "listing" && id && c.final_url && !c.final_url.includes(id))
            || (c.kind === "listing" && (c.listing_status === "not_found" || c.listing_status === "withdrawn"));
  if (gone) {
    // The listing page is dead (redirected to a property profile, 404, "no
    // longer available"). Before settling on WITHDRAWN, ask Domain's sold
    // search for the address - a sold listing's page often dies the same way.
    const base = { ...c, listing_status: "withdrawn",
                   status_basis: c.final_url && !c.final_url.includes(id) ? "chrome: redirected to " + c.final_url : c.status_basis };
    if (!(w.address && w.suburb)) return base;
    await sleep(DELAY_MS);
    const su = "https://www.domain.com.au/sold-listings/?" + new URLSearchParams({ street: `${w.address} ${w.suburb}`.toLowerCase() }).toString();
    const s = await openAndRead(su);
    if (s.kind !== "sold_search" || !s.count) return { ...base, status_basis: base.status_basis + "; not in Domain sold results either" };
    let historical = false;
    if (s.sold_date && w.first_seen) {
      const fs = new Date(w.first_seen), sd = new Date(s.sold_date);
      historical = sd < new Date(fs.getTime() - SOLD_DATE_GRACE_DAYS * 86400000);
    }
    if (historical) return { ...base, status_basis: base.status_basis + `; sold search shows only an older sale (${s.sold_date})`, resolved_url: s.resolved_url };
    return { ...c, listing_status: "sold", status_basis: "chrome: " + s.status_basis.replace(/^chrome: /, "") + " (listing page gone)",
             sold_date: s.sold_date, resolved_url: s.resolved_url };
  }
  if (c.kind === "search_count") {
    if (c.count === null) return { ...c, listing_status: "unknown" };
    if (c.count > 0) return { ...c, listing_status: "on_market" };
    // Gone from for-sale results: ask the sold search whether it sold.
    const su = soldSearchUrl(w.url);
    if (!su) return { ...c, listing_status: "withdrawn", status_basis: c.status_basis + " (no street param)" };
    await sleep(DELAY_MS);
    const s = await openAndRead(su);
    if (s.kind !== "sold_search" || !s.count) {
      return { ...c, listing_status: "withdrawn", status_basis: c.status_basis + "; not in Domain sold results either" };
    }
    let historical = false;
    if (s.sold_date && w.first_seen) {
      const fs = new Date(w.first_seen), sd = new Date(s.sold_date);
      historical = sd < new Date(fs.getTime() - SOLD_DATE_GRACE_DAYS * 86400000);
    }
    if (historical) {
      return { ...c, listing_status: "withdrawn",
               status_basis: c.status_basis + `; sold search shows only an older sale (${s.sold_date}, before first seen ${w.first_seen})`,
               resolved_url: s.resolved_url };
    }
    return { ...c, listing_status: "sold", status_basis: "chrome: " + s.status_basis.replace(/^chrome: /, ""),
             sold_date: s.sold_date, resolved_url: s.resolved_url };
  }
  return c;
}

async function postChecks(checks) {
  const r = await fetch(DASH + "/api/apply-status", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ checks }) });
  return r.json();
}

async function runVerify(cap, all) {
  if (state.running) return;
  setState({ running: true, total: 0, done: 0, log: [], result: null, error: null });
  try {
    const r = await fetch(`${DASH}/api/status-worklist?cap=${cap}${all ? "&all=1" : ""}`);
    if (!r.ok) throw new Error("dashboard not served? " + r.status);
    const wl = await r.json();
    const work = wl.worklist || [];
    setState({ total: work.length });
    log(`worklist: ${work.length} of ${wl.pending_total} pending`);
    const checks = [];
    for (const w of work) {
      const c = await verifyEntry(w);
      checks.push(stripCheck(c));
      setState({ done: checks.length });
      log(`${c.listing_status.padEnd(11)} ${w.address || ""}, ${w.suburb || ""}  (${(c.status_basis || "").replace(/^chrome: /, "")})`);
      await sleep(DELAY_MS);
      if (checks.length % 20 === 0) {          // apply in batches so progress survives interruption
        const res = await postChecks(checks.splice(0, checks.length));
        log(`applied batch: ${res.changed} change(s)${res.counts ? ` - sold ${res.counts.sold}, under offer ${res.counts.under_offer}, withdrawn ${res.counts.withdrawn}` : ""}`);
      }
    }
    let res = { changed: 0 };
    if (checks.length) res = await postChecks(checks);
    log(`applied: ${res.changed} change(s)${res.counts ? ` - sold ${res.counts.sold}, under offer ${res.counts.under_offer}, withdrawn ${res.counts.withdrawn}, active ${res.counts.total}` : ""}`);
    setState({ result: res });
  } catch (e) {
    setState({ error: e.message });
    log("error: " + e.message);
  } finally {
    setState({ running: false });
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === "verify-start") { runVerify(msg.cap || 60, !!msg.all); sendResponse({ ok: true }); }
  else if (msg?.type === "verify-state") { sendResponse(state); }
  return true;
});
