"""
render.py - regenerate 07-property-shortlist.md from the live listings.json.

Adam's resolution to SCOPE Q7 (option b): the dashboard regenerates
07-property-shortlist.md on each refresh, so the markdown can be copied into the
claude.ai version of the Sydney project for sharing with family. (This overrides
the SCOPE's own lean (a), which would have frozen 07.)

07 becomes a human-readable mirror of the current Tier-1-passing field, newest
sweep supersedes older. The HTML dashboard remains the interactive live record.

PURE COMPUTATION - reads listings.json, writes 07-property-shortlist.md.

CLI:
    python render.py [listings.json] [out_07.md]
Defaults: dashboard/data/listings.json -> 07-property-shortlist.md (project root).
"""

from __future__ import annotations
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LISTINGS = os.path.join(HERE, "..", "data", "listings.json")
DEFAULT_OUT = os.path.join(HERE, "..", "..", "07-property-shortlist.md")


def _price(lst):
    txt = lst.get("price_guide_text")
    if txt:
        return txt
    pmin, pmax = lst.get("price_min"), lst.get("price_max")
    if pmin and pmax and pmin != pmax:
        return f"${pmin:,.0f}-${pmax:,.0f}"
    if pmin:
        return f"${pmin:,.0f}"
    return "n/a"


def _ticks(lst):
    t1 = lst.get("tier1", {})
    order = ["budget", "property_type", "accessibility", "bedrooms",
             "transport", "supplies", "location", "zoning"]
    out = []
    for k in order:
        v = t1.get(k)
        if v is True:
            out.append(f"{k} OK")
        elif v is False:
            out.append(f"{k} FAIL")
        elif v is None:
            out.append(f"{k} ?")
    return ", ".join(out)


def render(data):
    listings = data.get("listings", [])
    gen = data.get("generated_at_sydney") or data.get("generated_at") or "unknown date"
    passing = [l for l in listings if l.get("tier1", {}).get("pass")]
    passing.sort(key=lambda l: l.get("tier2", {}).get("score", 0), reverse=True)
    near = [l for l in listings
            if not l.get("tier1", {}).get("pass")
            and not l.get("tier1", {}).get("fails")]  # only unverified, no hard fail
    near.sort(key=lambda l: l.get("tier2", {}).get("score", 0), reverse=True)

    L = []
    L.append("# Property Shortlist and Market Survey Notes")
    L.append("")
    L.append(f"*Auto-generated from the live dashboard sweep of **{gen}**. "
             "This page is regenerated on every refresh (decision: SCOPE Q7=b) and "
             "is a human-readable mirror of the interactive dashboard "
             "(`dashboard/index.html`), which remains the live record. "
             "Prices are agents' guides and must be re-verified before any action.*")
    L.append("")
    L.append("Governing criteria remain in `02-location-and-property-criteria.md` "
             "(Tier 1 hard parameters; Tier 2 discriminators per decision #17). "
             "Tier 1 marks: OK = pass, FAIL = deal-breaker, ? = needs manual "
             "verification (commonly step-free access / lift, which listings rarely state).")
    L.append("")
    c = data.get("counts", {})
    L.append(f"**This sweep:** {c.get('total', len(listings))} listings harvested; "
             f"{len(passing)} pass all determinable Tier 1 criteria; "
             f"{c.get('new', 0)} new, {c.get('price_changed', 0)} price-changed, "
             f"{c.get('sold', 0)} sold, {c.get('withdrawn', 0)} withdrawn since the prior sweep.")
    L.append("")

    L.append("## Tier 1 passing candidates (ranked by Tier 2 score)")
    L.append("")
    if passing:
        L.append("| Rank | Property | Type | Guide | Size | Beds | Outlook | T2 | Next open / auction | Agent |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for i, l in enumerate(passing, 1):
            outlook = (l.get("outlook") or {}).get("class", "-")
            ev = l.get("auction") or (l.get("open_homes") or [None])[0] or "-"
            size = f"{l['internal_m2']} m2" if l.get("internal_m2") else "-"
            L.append(f"| {i} | [{l.get('address','?')}, {l.get('suburb','')}]"
                     f"({l.get('url','')}) | {l.get('property_type','-')} | {_price(l)} | "
                     f"{size} | {l.get('beds','-')} | {outlook} | "
                     f"{l.get('tier2',{}).get('score','-')} | {ev} | "
                     f"{l.get('agency','-')} |")
    else:
        L.append("*No listings passed all determinable Tier 1 criteria in this sweep.*")
    L.append("")

    if near:
        L.append("## Near-miss candidates (no hard fail; Tier 1 items unverified)")
        L.append("")
        L.append("These need manual verification of the `?` items (usually step-free "
                 "access / lift) before they can be confirmed or excluded.")
        L.append("")
        L.append("| Property | Type | Guide | Outlook | T2 | Unverified | Agent |")
        L.append("|---|---|---|---|---|---|---|")
        for l in near[:20]:
            outlook = (l.get("outlook") or {}).get("class", "-")
            unv = ", ".join(l.get("tier1", {}).get("unverified", [])) or "-"
            L.append(f"| [{l.get('address','?')}, {l.get('suburb','')}]"
                     f"({l.get('url','')}) | {l.get('property_type','-')} | {_price(l)} | "
                     f"{outlook} | {l.get('tier2',{}).get('score','-')} | {unv} | "
                     f"{l.get('agency','-')} |")
        L.append("")

    failed = [l for l in listings if l.get("tier1", {}).get("fails")]
    if failed:
        L.append("## Excluded this sweep (Tier 1 hard fail)")
        L.append("")
        for l in failed[:30]:
            fails = ", ".join(l.get("tier1", {}).get("fails", []))
            L.append(f"- **{l.get('address','?')}, {l.get('suburb','')}** "
                     f"({_price(l)}) - fails: {fails}.")
        L.append("")

    L.append("## Standing rules")
    L.append("")
    L.append("- Verify zoning on the NSW Planning Portal Spatial Viewer before "
             "shortlisting any warehouse-character stock. E3/E4 listings are not "
             "lawful dwellings (decision #17).")
    L.append("- Tier 1 (step-free access, lift where multi-storey, flat terrain, "
             "1,500 m catchments, budget, location) is not negotiable; conversion "
             "character and outlook are Tier 2 discriminators only.")
    L.append("- Prices are agents' guides and require re-verification before any action.")
    L.append("")
    L.append(f"*Regenerated by `dashboard/scripts/render.py` from the {gen} sweep. "
             "Do not hand-edit - changes are overwritten on the next refresh.*")
    return "\n".join(L) + "\n"


def main(argv):
    listings_path = argv[1] if len(argv) > 1 else DEFAULT_LISTINGS
    out_path = argv[2] if len(argv) > 2 else DEFAULT_OUT
    with open(listings_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    md = render(data)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(f"Regenerated {out_path} from {data.get('generated_at_sydney') or 'sweep'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
