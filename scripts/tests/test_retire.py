"""Retirement of sold / no-longer-listed stock (22 Nov 2026).
Run: python scripts/tests/test_retire.py   (uses a temp archive; touches no data)"""
import os, sys, tempfile, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import sweep

T = "2026-11-22"
tmp = os.path.join(tempfile.mkdtemp(), "removed.json")


def rec(addr, flag="UNCHANGED", url=None, **kw):
    r = {"address": addr, "suburb": "Glebe", "change_flag": flag, "price_guide_text": "$1,200,000",
         "url": url or "https://www.domain.com.au/sale/?street=" + addr.replace(" ", "+").lower()}
    r.update(kw)
    return r


L = [rec("1 A St"), rec("2 B St"), rec("3 C St", "UNDER_OFFER"),
     rec("4 D St", url="https://www.domain.com.au/4-d-st-glebe-nsw-2037-2020111222")]

# "No exact matches" on the for-sale search + nothing in sold results => withdrawn => removed
n, _ = sweep.apply_status_checks(
    [{"url": L[0]["url"], "listing_status": "withdrawn",
      "status_basis": "chrome: address no longer in Domain for-sale results; not in Domain sold results either"},
     {"url": L[3]["url"], "listing_status": "sold", "status_basis": "chrome: Sold", "sold_date": "2026-11-20"},
     {"url": L[1]["url"], "listing_status": "on_market"},
     {"url": L[2]["url"], "listing_status": "unknown"}], L, T)
assert n == 2, n
kept, retired = sweep.retire_departed(L, T, tmp)
assert [r["address"] for r in retired] == ["1 A St", "4 D St"]
assert [k["address"] for k in kept] == ["2 B St", "3 C St"], "under offer + on market stay"
arch = sweep.load_removed(tmp)
assert len(arch) == 2 and arch[0]["removed_on"] == T and arch[0]["removed_as"] == "WITHDRAWN"
assert arch[1]["removed_as"] == "SOLD" and arch[1]["sold_date"] == "2026-11-20"
c = sweep.build_counts(kept)
assert c["sold"] == 0 and c["withdrawn"] == 0 and c["under_offer"] == 1 and c["total"] == 1

# idempotent; nothing to retire => archive untouched, same list object back
k2, r2 = sweep.retire_departed(kept, T, tmp)
assert k2 is kept and r2 == [] and len(sweep.load_removed(tmp)) == 2

# a re-served alert email cannot bring a removed listing back
new = [rec("1 A St"), rec("9 Z St"),
       rec("4 D St", url="https://www.domain.com.au/4-d-st-glebe-nsw-2037-2020111222")]
m = sweep.merge_incremental(new, [dict(x) for x in kept], T, removed=arch)
assert sorted(x["address"] for x in m) == ["2 B St", "3 C St", "9 Z St"], [x["address"] for x in m]
# ...after the block window a withdrawn address may return as NEW; a sold listing id never does
m = sweep.merge_incremental([rec("1 A St"), rec("4 D St", url=new[2]["url"])],
                            [dict(x) for x in kept], "2026-12-25", removed=arch)
assert sorted(x["address"] for x in m) == ["1 A St", "2 B St", "3 C St"]
assert [x for x in m if x["address"] == "1 A St"][0]["change_flag"] == "NEW"
# an explicit on-market page read (bookmarklet) re-adds at once
m = sweep.merge_incremental([rec("1 A St", listing_status="on_market")],
                            [dict(x) for x in kept], T, removed=arch)
assert "1 A St" in [x["address"] for x in m]

# restore
back = sweep.restore_removed("4 d st", kept, T, tmp)
assert len(back) == 1 and kept[-1]["address"] == "4 D St" and kept[-1]["change_flag"] == "UNCHANGED"
assert "removed_on" not in kept[-1] and len(sweep.load_removed(tmp)) == 1
print("test_retire: all assertions passed")
