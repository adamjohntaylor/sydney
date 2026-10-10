import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from outgoings import parse_outgoings as p

def q(t):
    r = p(t); return (r.get("strata_pq"), r.get("council_pq"), r.get("water_pq"))

def test_shared_trailing_period():
    assert q("- Water $178 | Council $495 | Strata $1,293 p/q") == (1293, 495, 178)

def test_malformed_label_order():
    assert q("Easy commute - Water $211 | Council $277 Strata | $2118 p/q") == (2118, 277, 211)

def test_each_with_period():
    assert q("Strata Rates - $1,226.09 per quarter approx. Council Rates - $390.00 per quarter approx. Water Rates - $201.90 per quarter approx. Rent $600 - $620 per week") == (1226.09, 390, 201.9)

def test_approximately_before_amount():
    r = p("Council rates: Approximately $321.00 per quarter Water rates: Approximately $153.56 per quarter Strata: Approximately $748.50 per quarter")
    assert (r["strata_pq"], r["council_pq"], r["water_pq"]) == (748.5, 321, 153.56)
    assert all(r["approx"].values())

def test_pq_attached_and_all_approx():
    r = p("- Rates: Water $185pq, Council $264pq, Strata $1643pq (All approx.)")
    assert (r["strata_pq"], r["council_pq"], r["water_pq"]) == (1643, 264, 185)

def test_quarterly_word_and_currently():
    assert q("Water Rates: $177 Quarterly approx\nCouncil Rates: $335 Quarterly approx\nStrata Levies: Currently $6,000 Quarterly approx") == (6000, 335, 177)

def test_spaced_dollar():
    assert q("Strata:    $1,689 pq\nCouncil: $   295 pq") == (1689, 295, None)

def test_outgoings_heading_period():
    assert q("Outgoings (Approx) Strata Levies: $1,013.45 per quarter Council Rates: $390.00 per quarter Water Rates: $210.00 per quarter") == (1013.45, 390, 210)

def test_slash_q():
    assert q("Outgoings (Approx.) Council rates: $320.50/Q Water rates: $210/Q Strata: $526.30/Q") == (526.3, 320.5, 210)

def test_annual_converted():
    assert q("Strata levies $8,000 p.a. Council rates $1,600 pa") == (2000, 400, None)

def test_unstated_period_not_annualised():
    r = p("Strata levy: $779.66")
    assert "strata_pq" not in r and r["unstated"]["strata"] == 779.66

def test_price_near_water_rejected():
    assert p("Sweeping water views. Price guide $1,850,000") == {}

def test_special_levy_separate():
    r = p("Strata $1,200 pq. Special levy $15,000 for facade works")
    assert r["strata_pq"] == 1200 and r["special_levy"] == 15000

def test_council_without_amount():
    assert p("Inner West Council approved DA for the rear studio.") == {}

def test_low_strata_phrase():
    r = p("Affordable price with Low strata levy of $779.66 per quarter")
    assert r["strata_pq"] == 779.66
