"""
status_probe.py - read the market status of tracked listings from their own
pages, so a sweep can mark SOLD / UNDER_OFFER / WITHDRAWN without waiting for
a bookmarklet click, a sold-alert email or a hand mark.

This is the deterministic reader behind sweep.py's verification leg:

    worklist = sweep.build_status_worklist(listings, today, cap=N)
    checks   = status_probe.probe_worklist(worklist)      # <- this module
    sweep.apply_status_checks(checks, listings, today)

Each probe is one polite GET of an individual listing page that is ALREADY on
the watchlist (browser User-Agent, one request at a time, a pause between
requests, a hard cap per run). It never crawls search results (decision #27).

Classification is conservative and listing-specific, mirroring the
bookmarklet's banner read: JSON-LD offers.availability, the sold/under-offer
badge, the "SOLD - $" heading, Domain's "Sold by private treaty/auction"
stamp, REA's /sold/ URL move, and "no longer available" removal pages.
Anything ambiguous returns 'unknown' (no change to the record) rather than a
guess - a wrong SOLD would hide a live property.

CLI:
    python scripts/status_probe.py --cap 60              # probe + apply + write listings.json
    python scripts/status_probe.py --cap 250 --dry-run   # probe only, print verdicts
    python scripts/status_probe.py --url <listing url>   # one page, print verdict
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.normpath(os.path.join(HERE, ".."))
DATA = os.path.join(DASH, "data")
sys.path.insert(0, HERE)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
TIMEOUT = 20            # seconds per page
DELAY = 1.0             # seconds between requests
DEFAULT_CAP = 60        # pages per run (Refresh uses this)
MAX_BYTES = 2_500_000

# ---- page-evidence patterns (order matters: sold > under offer > removed) ----
_RE_SOLDOUT_LD = re.compile(r'"availability"\s*:\s*"(?:https?://schema\.org/)?SoldOut"', re.I)
_RE_INSTOCK_LD = re.compile(r'"availability"\s*:\s*"(?:https?://schema\.org/)?InStock"', re.I)
_RE_SOLD_STAMP = re.compile(r"Sold\s+(?:by\s+private\s+treaty|at\s+auction|prior\s+to\s+auction|"
                            r"by\s+auction)\b[^<]{0,40}", re.I)
_RE_SOLD_HEAD = re.compile(r">\s*SOLD\s*(?:-|&ndash;|–)\s*(?:\$[\d,\.]+[kKmM]?|price\s+withheld|"
                           r"undisclosed)", re.I)
_RE_SOLD_TITLE = re.compile(r"<title>[^<]*\bsold\b[^<]*</title>", re.I)
_RE_SOLD_PRICE_KEY = re.compile(r'"(?:soldPrice|sold_price|soldDate|dateSold)"\s*:\s*"?[^",}]{2,}', re.I)
_RE_UNDER_OFFER = re.compile(r">\s*(?:Under\s+offer|Under\s+contract|Deposit\s+taken|"
                             r"Contract\s+exchanged)\s*<", re.I)
_RE_REMOVED = re.compile(r"(?:no\s+longer\s+available|listing\s+(?:has\s+been\s+)?removed|"
                         r"property\s+(?:is\s+)?not\s+available|this\s+listing\s+has\s+expired|"
                         r"page\s+not\s+found|couldn.t\s+find\s+(?:that|the)\s+(?:page|property))", re.I)
_RE_BLOCKED = re.compile(r"(?:<title>[^<]*(?:Access\s+Denied|Just\s+a\s+moment|Attention\s+Required|"
                         r"Pardon\s+Our\s+Interruption)[^<]*</title>|captcha|cf-challenge|"
                         r"px-captcha|_Incapsula_)", re.I)
_RE_PRICE_TEXT = re.compile(r"\$\s?\d{1,3}(?:,\d{3}){1,2}|\$\s?\d(?:\.\d+)?\s?[mM]\b")
# Domain titles: live = "<address> | Domain"; sold = "Sold <address> on 19 Jun 2026 - <id> | Domain"
_RE_TITLE_SOLD_DOMAIN = re.compile(r"<title>\s*Sold\s+\S[^<]*?\bon\s+\d{1,2}\s+\w{3,9}\s+\d{4}", re.I)
# Domain search-results title: "1 Real Estate Property for Sale | Domain" / "0 Real Estate Properties ..."
_RE_SEARCH_COUNT = re.compile(r"<title>\s*(\d[\d,]*)\s+Real\s+Estate\s+Propert", re.I)
_RE_SEARCH_LISTING_HREF = re.compile(r'href="(https://www\.domain\.com\.au/[a-z0-9\-]+-\d{6,12})"', re.I)
# Zero-result for-sale search: title has NO leading count ("Real Estate Properties for Sale | Domain")
_RE_SEARCH_ZERO = re.compile(r"<title>\s*Real\s+Estate\s+Propert(?:y|ies)\s+for\s+Sale|No\s+exact\s+matches", re.I)
# Sold search ("/sold-listings/?street=..."): "1 Property Sold & Auction Results | Domain"
_RE_SOLD_SEARCH_COUNT = re.compile(r"<title>\s*(\d[\d,]*)\s+Propert(?:y|ies)\s+Sold", re.I)
_RE_DATE = re.compile(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b", re.I)
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                                       "sep", "oct", "nov", "dec"), start=1)}
SOLD_DATE_GRACE_DAYS = 60   # a sale dated well before we first saw the listing is a historical sale


def sold_search_url(search_url):
    """Domain for-sale single-address search -> the matching sold-listings search."""
    from urllib.parse import urlparse, parse_qs, urlencode
    q = parse_qs(urlparse(search_url).query)
    street = (q.get("street") or [""])[0]
    if not street:
        return None
    return "https://www.domain.com.au/sold-listings/?" + urlencode({"street": street})


def latest_date_in(html):
    """Newest 'd Mon yyyy' date in the html as ISO, or None."""
    import datetime as _dt
    best = None
    for d, mon, y in _RE_DATE.findall(html or ""):
        try:
            dt_ = _dt.date(int(y), _MONTHS[mon.lower()[:3]], int(d))
        except (ValueError, KeyError):
            continue
        if best is None or dt_ > best:
            best = dt_
    return best.isoformat() if best else None


def is_search_url(url):
    u = (url or "").lower()
    return ("/sale/?" in u or "street=" in u or "/results" in u or "excludeunderoffer" in u)


def _listing_id(url):
    m = re.search(r"(?:-|/)(\d{6,12})/?(?:\?.*)?$", url or "")
    return m.group(1) if m else None


def fetch(url, timeout=TIMEOUT):
    """GET url -> (http_status, final_url, html_or_empty, error_or_None)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-AU,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.status, resp.geturl(), body.decode(charset, errors="replace"), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read(MAX_BYTES).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = ""
        return e.code, getattr(e, "url", url) or url, body, None
    except Exception as e:  # noqa: BLE001  (timeout, DNS, TLS, connection reset)
        return 0, url, "", f"{type(e).__name__}: {e}"


def classify(status, final_url, html, url):
    """-> (listing_status, status_basis). listing_status uses sweep.py's
    vocabulary: sold | under_offer | withdrawn | not_found | redirected |
    on_market | unknown | blocked | error."""
    if status == 0:
        return "error", "fetch failed"
    if status in (404, 410):
        return "not_found", f"HTTP {status}"
    if status in (403, 429, 503) or _RE_BLOCKED.search(html or ""):
        return "blocked", f"HTTP {status} / bot challenge"
    if status >= 400:
        return "unknown", f"HTTP {status}"
    h = html or ""
    lid = _listing_id(url)

    # A single-address Domain search URL (alert-derived records that never got a
    # direct listing URL): the result count IS the market status of that address.
    if is_search_url(url):
        m = _RE_SEARCH_COUNT.search(h)
        if m:
            n = int(m.group(1).replace(",", ""))
        elif _RE_SEARCH_ZERO.search(h):
            n = 0
        else:
            return "unknown", "search page without a result count"
        if n == 0:
            # 'gone' is the verdict here; probe_url follows up with the sold
            # search to tell SOLD from WITHDRAWN.
            return "gone", "address no longer in Domain for-sale results"
        return "on_market", f"{n} for-sale result(s) for the address"

    if _RE_TITLE_SOLD_DOMAIN.search(h):
        m = _RE_SOLD_STAMP.search(h)
        return "sold", "page title 'Sold ... on <date>'" + (f"; {m.group(0).strip()}" if m else "")

    # A different listing id at the end of the road means we were bounced to
    # another property / a search page: the original is gone.
    fid = _listing_id(final_url)
    if fid and lid and fid != lid:
        return "redirected", f"redirected to {final_url}"
    if lid and lid not in final_url and ("/sale/" in final_url or "/buy/" in final_url
                                         or "/results" in final_url or final_url.rstrip("/").endswith(
                                             (".com.au", "/nsw", "/glebe", "/balmain"))):
        return "redirected", f"redirected to {final_url}"

    # REA moves sold listings under /sold/; Domain keeps the URL but stamps the page.
    if "/sold/" in final_url and lid and lid in final_url:
        return "sold", "URL moved under /sold/"
    if _RE_SOLDOUT_LD.search(h):
        m = _RE_SOLD_STAMP.search(h)
        return "sold", ("JSON-LD SoldOut; " + m.group(0).strip()) if m else "JSON-LD availability SoldOut"
    m = _RE_SOLD_STAMP.search(h)
    if m and (_RE_SOLD_HEAD.search(h) or _RE_SOLD_TITLE.search(h) or _RE_SOLD_PRICE_KEY.search(h)):
        return "sold", m.group(0).strip()
    if _RE_SOLD_HEAD.search(h) and _RE_SOLD_TITLE.search(h):
        return "sold", "SOLD heading + sold page title"
    if _RE_UNDER_OFFER.search(h):
        return "under_offer", _RE_UNDER_OFFER.search(h).group(0).strip("<> ").strip()
    if _RE_REMOVED.search(h) and not _RE_INSTOCK_LD.search(h):
        return "withdrawn", _RE_REMOVED.search(h).group(0).strip()
    if _RE_INSTOCK_LD.search(h):
        return "on_market", "JSON-LD availability InStock"
    # Live page with the listing's own id in the final URL and a price shown:
    # treat as on market. Otherwise say so honestly.
    if lid and lid in final_url and len(h) > 20_000 and _RE_PRICE_TEXT.search(h):
        return "on_market", "listing page rendered with price"
    if lid and lid in final_url and len(h) > 20_000:
        return "on_market", "listing page rendered (no price text)"
    return "unknown", f"HTTP {status}, no recognisable status signal"


def probe_url(url, timeout=TIMEOUT, first_seen=None, delay=DELAY):
    status, final_url, html, err = fetch(url, timeout)
    listing_status, basis = classify(status, final_url, html, url)
    if err:
        basis = err
    chk = {"url": url, "listing_status": listing_status, "status_basis": f"probe: {basis}",
           "http_status": status}
    if listing_status == "gone":
        # Left the for-sale results: ask Domain's sold search whether it sold.
        chk["listing_status"], chk["status_basis"] = "withdrawn", f"probe: {basis} (sold search not run)"
        su = sold_search_url(url)
        if su:
            time.sleep(delay)
            s2, f2, h2, e2 = fetch(su, timeout)
            m = _RE_SOLD_SEARCH_COUNT.search(h2 or "")
            n2 = int(m.group(1).replace(",", "")) if m else 0
            if e2 or s2 >= 400:
                chk["status_basis"] = f"probe: {basis}; sold search failed ({e2 or s2})"
            elif n2 == 0:
                chk["status_basis"] = f"probe: {basis}; not in Domain sold results either"
            else:
                sold_date = latest_date_in(h2)
                href = _RE_SEARCH_LISTING_HREF.search(h2 or "")
                historical = False
                if sold_date and first_seen:
                    import datetime as _dt
                    try:
                        fs = _dt.date.fromisoformat(first_seen[:10])
                        historical = _dt.date.fromisoformat(sold_date) < fs - _dt.timedelta(days=SOLD_DATE_GRACE_DAYS)
                    except ValueError:
                        historical = False
                if historical:
                    chk["status_basis"] = (f"probe: {basis}; sold search shows only an older sale "
                                           f"({sold_date}, before first seen {first_seen})")
                else:
                    chk["listing_status"] = "sold"
                    chk["status_basis"] = (f"probe: Domain sold results list the address"
                                           + (f", sold {sold_date}" if sold_date else ""))
                    if sold_date:
                        chk["sold_date"] = sold_date
                if href:
                    chk["resolved_url"] = href.group(1)
    if final_url and final_url != url:
        chk["final_url"] = final_url
    if chk["listing_status"] == "on_market":
        m = re.search(r'"price"\s*:\s*"([^"]{2,40})"', html or "")
        if m and _RE_PRICE_TEXT.search(m.group(1)):
            chk["price_guide_text"] = m.group(1)
        if is_search_url(url):
            m = _RE_SEARCH_LISTING_HREF.search(html or "")
            if m:
                chk["resolved_url"] = m.group(1)   # direct listing URL for the record
    return chk


def probe_worklist(worklist, cap=DEFAULT_CAP, delay=DELAY, timeout=TIMEOUT, log=None):
    """Probe each worklist entry's url in order (cap, delay honoured).
    Returns a list of check records for sweep.apply_status_checks."""
    checks = []
    for i, w in enumerate(worklist[:cap] if cap else worklist):
        if i:
            time.sleep(delay)
        chk = probe_url(w["url"], timeout, first_seen=w.get("first_seen"), delay=delay)
        chk["address"], chk["suburb"] = w.get("address"), w.get("suburb")
        chk["worklist_reason"] = w.get("reason")
        checks.append(chk)
        if log:
            log(f"  [{i+1}/{min(cap, len(worklist)) if cap else len(worklist)}] "
                f"{chk['listing_status']:<12} {w.get('address') or ''}, {w.get('suburb') or ''}"
                f"  ({chk['status_basis']})")
    return checks


def run_verification(listings, today, cap=DEFAULT_CAP, include_all=False, log=None, delay=DELAY):
    """Worklist -> probe -> apply, in place. Returns a summary dict."""
    import sweep as sweep_mod
    work = sweep_mod.build_status_worklist(listings, today, cap=cap, include_all=include_all)
    pending = len(sweep_mod.build_status_worklist(listings, today, cap=0, include_all=include_all))
    if log:
        log(f"Status probe: {len(work)} of {pending} pending pages")
    checks = probe_worklist(work, cap=cap, delay=delay, log=log)
    changed, details = sweep_mod.apply_status_checks(checks, listings, today)
    from collections import Counter
    verdicts = Counter(c["listing_status"] for c in checks)
    return {"probed": len(checks), "pending_before": pending, "changed": changed,
            "verdicts": dict(verdicts), "details": details, "checks": checks}


def archive_checks(checks, summary_note, stamp):
    """Write the probe's checks under data/status-checks/ pre-marked applied (audit)."""
    d = os.path.join(DATA, "status-checks")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"status-{stamp}-probe.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"source": "status_probe", "note": summary_note, "checks": checks},
                  fh, indent=2, ensure_ascii=False)
    with open(path + ".applied", "w", encoding="utf-8") as fh:
        fh.write(summary_note + "\n")
    return path


def main(argv):
    ap = argparse.ArgumentParser(description="Probe listing pages for sold / withdrawn status.")
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP)
    ap.add_argument("--all", action="store_true", help="include every active listing")
    ap.add_argument("--delay", type=float, default=DELAY)
    ap.add_argument("--dry-run", action="store_true", help="probe and print, do not write")
    ap.add_argument("--url", help="probe a single url and print the verdict")
    ap.add_argument("--listings", default=os.path.join(DATA, "listings.json"))
    args = ap.parse_args(argv[1:])

    if args.url:
        print(json.dumps(probe_url(args.url), indent=2))
        return 0

    import sweep as sweep_mod
    with open(args.listings, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    listings = data.get("listings", [])
    syd = sweep_mod.now_sydney()
    today = syd.date().isoformat()
    log = lambda s: print(s, file=sys.stderr, flush=True)   # noqa: E731
    if args.dry_run:
        work = sweep_mod.build_status_worklist(listings, today, cap=args.cap, include_all=args.all)
        for c in probe_worklist(work, cap=args.cap, delay=args.delay, log=log):
            pass
        return 0
    res = run_verification(listings, today, cap=args.cap, include_all=args.all, log=log,
                           delay=args.delay)
    note = (f"probe {today}: {res['probed']} probed, {res['changed']} flag change(s), "
            f"verdicts {res['verdicts']}")
    archive_checks(res["checks"], note, syd.strftime("%Y%m%dT%H%M"))
    data["counts"] = sweep_mod.build_counts(listings)
    with open(args.listings, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    try:
        import render as render_mod
        with open(os.path.join(DASH, "..", "07-property-shortlist.md"), "w", encoding="utf-8") as fh:
            fh.write(render_mod.render(data))
    except Exception:  # noqa: BLE001
        pass
    print(note)
    for d in res["details"]:
        print("  " + d)
    print("counts:", data["counts"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
