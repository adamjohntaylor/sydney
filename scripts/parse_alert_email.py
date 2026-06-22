#!/usr/bin/env python3
"""
parse_alert_email.py - turn Domain / realestate.com.au saved-search ALERT EMAILS
into the start of a harvest file for sweep.py.

Ingestion route A (decision #27): instead of scraping Domain (ToS-prohibited and
anti-bot-blocked), the dashboard ingests the saved-search alert emails Adam
receives. A sweep reads those emails via the Gmail / Microsoft-365 connector;
Claude extracts the per-listing fields (it parses email content far more reliably
than brittle template regex). THIS script is the deterministic backbone: given an
email body (HTML or text), it pulls out the listing URLs and any price/address
text sitting next to them, and emits a de-duplicated harvest skeleton. Claude then
fills the remaining fields (beds/baths/m2/outlook/zoning/geocode) and runs sweep.py.

It is intentionally conservative: it never invents data. Fields it cannot read
are left absent so score.py treats them as "unverified", not failed.

CLI:
    python parse_alert_email.py email1.html [email2.html ...] > harvest.json
    cat email.txt | python parse_alert_email.py - > harvest.json
"""

from __future__ import annotations
import datetime as dt
import html
import json
import re
import sys

# Listing-URL patterns. Domain ids are 7-10 digits at the end of a slug; REA ids
# trail "property-...-<id>" or "-<id>" forms. Kept loose to survive template drift.
DOMAIN_RE = re.compile(r"https?://(?:www\.)?domain\.com\.au/[A-Za-z0-9\-/]*?-?(\d{7,12})\b")
REA_RE = re.compile(r"https?://(?:www\.)?realestate\.com\.au/(?:property[A-Za-z0-9\-/]*?|[A-Za-z0-9\-/]*?)-(\d{6,12})\b")
PRICE_RE = re.compile(r"\$[\d][\d,]*(?:\.\d+)?\s*(?:[kKmM]|million)?(?:\s*-\s*\$[\d][\d,]*(?:\.\d+)?\s*(?:[kKmM]|million)?)?")
SUBURB_HINT = re.compile(r"\b(Zetland|Alexandria|Erskineville|Newtown|Camperdown|Glebe|"
                         r"Annandale|Leichhardt|Lilyfield|Rozelle|Balmain|Birchgrove|"
                         r"Marrickville|Dulwich Hill|Petersham|Stanmore|Enmore|Drummoyne)\b", re.I)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t ]+")


def strip_html(body: str) -> str:
    # Drop scripts/styles, turn block tags into newlines, unescape entities.
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?i)<(br|/p|/div|/tr|/td|/li|/h\d)[^>]*>", "\n", body)
    text = TAG_RE.sub(" ", body)
    text = html.unescape(text)
    text = WS_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def parse_price(text):
    """Return (price_text, price_min, price_max) from a price-ish string, or Nones."""
    if not text:
        return None, None, None
    m = PRICE_RE.search(text)
    if not m:
        return None, None, None
    raw = m.group(0)

    def to_num(tok):
        tok = tok.strip().lower().replace("$", "").replace(",", "").replace("million", "m")
        mult = 1
        if tok.endswith("m"):
            mult, tok = 1_000_000, tok[:-1]
        elif tok.endswith("k"):
            mult, tok = 1_000, tok[:-1]
        try:
            return int(float(tok) * mult)
        except ValueError:
            return None

    parts = [p for p in re.split(r"\s*-\s*", raw) if p.strip()]
    nums = [to_num(p) for p in parts]
    nums = [n for n in nums if n]
    if not nums:
        return raw, None, None
    return raw, min(nums), max(nums)


ANCHOR_RE = re.compile(r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)


def _clean_url(url):
    return html.unescape(url).rstrip(").,;\"'")


def extract(body: str):
    """Extract listing URLs from the RAW body (so href attributes survive), with
    best-effort address (anchor text) and price/suburb from surrounding text."""
    is_html = "<" in body and ">" in body
    found = {}  # url -> partial listing

    def listing_for(url):
        return {"url": url, "source": "domain" if "domain.com.au" in url else "realestate"}

    def is_listing_url(url):
        return bool(DOMAIN_RE.search(url) or REA_RE.search(url))

    # 1) Anchors: capture url + visible address text + a context window after it.
    if is_html:
        for m in ANCHOR_RE.finditer(body):
            url = _clean_url(m.group(1))
            if not is_listing_url(url) or url in found:
                continue
            lst = listing_for(url)
            addr = WS_RE.sub(" ", TAG_RE.sub(" ", html.unescape(m.group(2)))).strip()
            if addr and len(addr) < 160:
                lst["address_text"] = addr
                sub = SUBURB_HINT.search(addr)
                if sub:
                    lst["suburb"] = sub.group(0).title()
            window = body[m.end(): m.end() + 600]
            ptext, pmin, pmax = parse_price(WS_RE.sub(" ", TAG_RE.sub(" ", window)))
            if ptext:
                lst["price_guide_text"] = ptext
                if pmin:
                    lst["price_min"] = pmin
                if pmax:
                    lst["price_max"] = pmax
            if "suburb" not in lst:
                sub = SUBURB_HINT.search(WS_RE.sub(" ", TAG_RE.sub(" ", window)))
                if sub:
                    lst["suburb"] = sub.group(0).title()
            found[url] = lst

    # 2) Bare URLs anywhere in the raw body (plain-text emails, un-anchored links).
    for rgx in (DOMAIN_RE, REA_RE):
        for m in rgx.finditer(body):
            url = _clean_url(m.group(0))
            if url not in found:
                found[url] = listing_for(url)

    return list(found.values())


def main(argv):
    args = argv[1:]
    bodies = []
    if not args or args == ["-"]:
        bodies.append(sys.stdin.read())
    else:
        for path in args:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                bodies.append(fh.read())

    listings = {}
    for body in bodies:
        for lst in extract(body):
            listings.setdefault(lst["url"], lst)

    out = {
        "generated_at_sydney": dt.datetime.now(
            dt.timezone(dt.timedelta(hours=10))).strftime("%Y-%m-%d %H:%M AEST (Sydney)"),
        "sweep_provenance": ("Email-alert ingestion (route A): parsed from Domain/REA "
                             "saved-search alert emails. Fields beyond URL/price/suburb "
                             "to be enriched by Claude before scoring."),
        "listings": list(listings.values()),
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"# extracted {len(listings)} unique listing URLs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
