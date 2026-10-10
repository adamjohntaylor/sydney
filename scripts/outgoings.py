"""Outgoings (strata levies, council rates, water rates) read from listing text.

Finding (10 Oct 2026): on Domain and realestate.com.au these figures are NOT a
standard structured field. Agents type them into the visible description, in
free and inconsistent forms, e.g.

    "Strata Levies: $3,228 Quarterly approx"
    "Water $178 | Council $495 | Strata $1,293 p/q"      (one shared period)
    "Rates: Water $185pq, Council $264pq, Strata $1643pq (All approx.)"
    "Water $211 | Council $277 Strata | $2118 p/q"       (label on wrong side)
    "Council:  $   295 pq"

so this module parses text. Whether either portal ALSO carries them as hidden
page-data keys is unverified: the page reader records any candidate keys it
sees (`outgoings_keys`) so one Verify run settles that empirically; until then
those values are logged, never used.

Rules (conservative - an unread figure is `?`, never a guess):
  * a figure needs an explicit label (strata / levies / body corporate /
    council / water) and a $ amount after it, with no other label between;
  * the period comes from text after the amount (pq, p/q, quarterly, per
    quarter, pa, per annum, monthly ...); failing that, from a period word
    that closes the same run of figures ("... Strata $1,293 p/q"), or opens it
    ("Outgoings per quarter: ..."). If no period is stated the figure is kept
    with period "unstated" and is NOT annualised or scored;
  * plausibility bands per quarter reject prices, rents and other $ amounts
    that happen to sit near a label;
  * "special levy" amounts are one-off and are reported separately, never as
    the ordinary strata levy.
"""
import re

KINDS = ("strata", "council", "water")

_LABELS = [
    # order matters: longer/more specific first
    ("special", r"special\s+lev(?:y|ies)"),
    ("strata", r"strata(?:\s+(?:levies|levy|fees?|rates?|contributions?|costs?))?"
               r"|body\s+corp(?:orate)?(?:\s+fees?)?|owners?\s+corp(?:oration)?(?:\s+fees?)?"
               r"|(?<!special\s)levies|(?<!special\s)levy"),
    ("council", r"council(?:\s+rates?)?"),
    ("water", r"water(?:\s+(?:and|&)\s+sewer(?:age)?)?(?:\s+rates?|\s+charges?|\s+usage)?|sydney\s+water"),
]
_LABEL_RE = re.compile("|".join(f"(?P<{k}>\\b(?:{p})\\b)" for k, p in _LABELS), re.I)

_AMOUNT_RE = re.compile(r"\$\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?!\d)")

_PERIODS = [
    ("quarter", r"p\s*/\s*q\b|pq\b|p\.q\.?|per\s+qtr|/\s*qtr\b|/\s*q\b|quarterly|per\s+quarter|a\s+quarter|each\s+quarter|qtr\b"),
    ("annual", r"p\s*/\s*a\b|pa\b|p\.a\.?|per\s+annum|per\s+year|a\s+year|/\s*yr\b|/\s*year\b|annually|yearly"),
    ("month", r"p\s*/\s*m\b|pcm\b|per\s+month|/\s*month\b|monthly"),
]
_PERIOD_RE = re.compile("|".join(f"(?P<{k}>{p})" for k, p in _PERIODS), re.I)
_TO_QUARTER = {"quarter": 1.0, "annual": 0.25, "month": 3.0}

# Plausible per-quarter ranges (NSW, 2026). Outside -> rejected as not-an-outgoing.
_BANDS_PQ = {"strata": (100, 25_000), "council": (100, 3_500), "water": (40, 1_500),
             "special": (100, 500_000)}
_APPROX_RE = re.compile(r"approx|approximately|circa|\bc\.|\best(?:imated)?\b|~", re.I)
# Characters allowed between a label and its amount (separators / filler words).
_GAP_OK = re.compile(r"^[\s:\-–—|=•·*,.()\[\]]*(?:(?:approx(?:imately|\.)?|currently|"
                     r"around|about|circa|est(?:imated)?|is|are|of|at|only|just|total|"
                     r"per\s+quarter|quarterly|p/?q|pa|p\.a\.?|per\s+annum)[\s:\-–—|=•·*,.()]*)*$", re.I)


def _num(m):
    whole = int(m.group(1).replace(",", ""))
    frac = m.group(2)
    return whole + (int(frac) / (10 ** len(frac)) if frac else 0.0)


def _period_at(text, start, limit):
    """Period word in text[start:limit], allowing only a short run of filler."""
    seg = text[start:limit]
    m = _PERIOD_RE.search(seg)
    if not m:
        return None, None
    lead = seg[:m.start()]
    if len(lead) > 28 or "$" in lead:
        return None, None
    return m.lastgroup, start + m.end()


def parse_outgoings(text):
    """Return {strata_pq, council_pq, water_pq, special_levy, periods, approx,
    snippet, notes} with only the figures actually found. `<kind>_pq` is set
    only when the period was stated; an unstated period is reported in
    `unstated` as the raw amount."""
    if not text:
        return {}
    t = re.sub(r"[ \t\u00a0]+", " ", str(text))
    labels = [(m.start(), m.end(), m.lastgroup) for m in _LABEL_RE.finditer(t)]
    if not labels:
        return {}
    hits = []   # (kind, amount, amt_end, period, label_start, approx)
    for i, (ls, le, kind) in enumerate(labels):
        nxt = labels[i + 1][0] if i + 1 < len(labels) else len(t)
        window_end = min(nxt, le + 70)
        am = _AMOUNT_RE.search(t, le, window_end)
        if not am:
            continue
        gap = t[le:am.start()]
        if not _GAP_OK.match(gap):
            continue
        amount = _num(am)
        period, _ = _period_at(t, am.end(), min(nxt, am.end() + 40))
        if period is None:
            # A period word sitting between label and amount ("Strata per quarter: $x").
            pm = _PERIOD_RE.search(gap)
            if pm:
                period = pm.lastgroup
        approx = bool(_APPROX_RE.search(t[max(0, ls - 5):min(len(t), am.end() + 25)]))
        hits.append([kind, amount, am.end(), period, ls, approx])
    if not hits:
        return {}
    # Shared period: a run of figures closed by one period word ("... $1,293 p/q")
    # or opened by one ("Outgoings per quarter: ...", "Quarterly outgoings: ...").
    runs, cur = [], [hits[0]]
    for h in hits[1:]:
        if h[4] - cur[-1][2] <= 60 and "\n\n" not in t[cur[-1][2]:h[4]]:
            cur.append(h)
        else:
            runs.append(cur); cur = [h]
    runs.append(cur)
    for run in runs:
        stated = {h[3] for h in run if h[3]}
        shared = stated.pop() if len(stated) == 1 else None
        if shared is None and not stated:
            head = t[max(0, run[0][4] - 60):run[0][4]]
            pm = None
            for pm in _PERIOD_RE.finditer(head):
                pass
            if pm:
                shared = pm.lastgroup
        if shared:
            for h in run:
                if not h[3]:
                    h[3] = shared
        if any(h[5] for h in run) and re.search(r"all\s+approx|\(approx", t[run[-1][2]:run[-1][2] + 30], re.I):
            for h in run:
                h[5] = True
    out = {"periods": {}, "approx": {}, "unstated": {}, "notes": []}
    snippet_lo, snippet_hi = None, None
    for kind, amount, aend, period, ls, approx in hits:
        if kind in out["periods"] or kind in out["unstated"] or (kind == "special" and "special_levy" in out):
            continue   # first figure per kind wins
        pq = amount * _TO_QUARTER[period] if period else None
        lo, hi = _BANDS_PQ[kind]
        test = pq if pq is not None else amount
        if not (lo <= test <= hi):
            out["notes"].append(f"{kind} ${amount:,.0f} rejected (outside plausible range)")
            continue
        if kind == "special":
            out["special_levy"] = round(amount, 2)
        elif pq is None:
            out["unstated"][kind] = round(amount, 2)
        else:
            out[f"{kind}_pq"] = round(pq, 2)
            out["periods"][kind] = period
        out["approx"][kind] = approx
        snippet_lo = ls if snippet_lo is None else min(snippet_lo, ls)
        snippet_hi = aend if snippet_hi is None else max(snippet_hi, aend)
    if snippet_lo is not None:
        out["snippet"] = re.sub(r"\s+", " ", t[snippet_lo:min(len(t), snippet_hi + 20)]).strip()[:240]
    for k in ("periods", "approx", "unstated", "notes"):
        if not out[k]:
            out.pop(k)
    return out if any(k.endswith("_pq") or k in ("unstated", "special_levy") for k in out) else {}


def apply_outgoings(target, parsed, source, today):
    """Write parsed outgoings onto a listing record. Returns audit strings.
    A manual (drawer) figure is never overwritten by the page reader."""
    out = []
    if target.get("outgoings_source", "").startswith("manual"):
        return out
    if not parsed:
        return out
    changed = False
    for k in KINDS:
        v = parsed.get(f"{k}_pq")
        if v is not None and target.get(f"{k}_pq") != v:
            out.append(f"{k}_pq: {target.get(f'{k}_pq')}->{v}")
            target[f"{k}_pq"] = v
            changed = True
    if parsed.get("unstated"):
        target["outgoings_unstated"] = parsed["unstated"]
    if parsed.get("special_levy"):
        target["special_levy"] = parsed["special_levy"]
    if changed or parsed.get("unstated") or parsed.get("special_levy"):
        target["outgoings_source"] = source
        target["outgoings_snippet"] = parsed.get("snippet")
        target["outgoings_approx"] = any((parsed.get("approx") or {}).values())
        target["outgoings_read_on"] = today
    derive_annuals(target)
    return out


def derive_annuals(target):
    """strata_pa feeds score.py's soft Tier 2 strata penalty (further-check-A);
    outgoings_pa is the sum of whichever of the three are known."""
    s = target.get("strata_pq")
    if isinstance(s, (int, float)):
        target["strata_pa"] = round(s * 4)
    known = [target.get(f"{k}_pq") for k in KINDS if isinstance(target.get(f"{k}_pq"), (int, float))]
    if known:
        target["outgoings_pa"] = round(sum(known) * 4)
        target["outgoings_known"] = [k for k in KINDS if isinstance(target.get(f"{k}_pq"), (int, float))]
    return target
