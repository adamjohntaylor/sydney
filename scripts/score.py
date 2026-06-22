"""
score.py - Tier 1 (boolean pass/fail) + Tier 2 (weighted 0-100) scoring for the
Sydney Inner West live-properties dashboard.

Encodes the suitability criteria from:
  02-location-and-property-criteria.md  (Tier 1 / Tier 2 / Tier 3)
  05-decision-log.md  decisions #5, #9, #14, #15, #17
  dashboard/SCOPE.md  section 3

Adam's resolved ambiguities (SCOPE section 4):
  Q2  property-type scope = (i) Tier-1-compliant apartments + 2BR cottages
       PLUS warehouse-conversion apartments meeting Tier 1.   (raw shells excluded)
  Q3  walkability = Euclidean 1,500 m (this module). OSRM routed distance is a
       shortlist-stage follow-on, not computed here.
  further-check-A  strata levy = SOFT Tier 2 penalty (not a hard ceiling).
  further-check-B  pool 1,500 m stays in Tier 2.

This module is PURE COMPUTATION. It performs no network I/O. Geocoding, the NSW
zoning lookup, the Domain harvest and the outlook auto-classification are done by
Claude (via Claude-in-Chrome / web_fetch) at sweep time and supplied on each
listing dict before scoring. See dashboard/RUNBOOK.md.

CLI:
    python score.py <listings_in.json> <osm_amenities.geojson> [listings_out.json]
Reads a list (or {"listings":[...]}) of harvested+enriched listing dicts, computes
catchments + tier1 + tier2 in place, prints/writes the scored list.
"""

from __future__ import annotations
import json
import math
import re
import sys

# ---------------------------------------------------------------------------
# Constants from the criteria
# ---------------------------------------------------------------------------

BUDGET_CEILING = 2_200_000          # decision #9
CATCHMENT_M = 1_500                 # 02: recurring 1,500 m walkability radius
LIVING_AREA_TARGET_M2 = 115         # decision #17
STRATA_BASELINE_PA = 12_000         # ROA models ~$12k p.a. initially (02)

# Outlook quality ranking (decision #17 - leading Tier 2 discriminator).
# water > park > elevated district > leafy > city > none
OUTLOOK_SCORE = {
    "water": 1.00,
    "park": 0.80,
    "elevated_district": 0.65,
    "leafy": 0.50,
    "city": 0.45,
    "none": 0.10,
    None: 0.10,
}

# Tier 2 weights (sum of positive weights = 100). Outlook is "leading".
WEIGHTS = {
    "outlook": 30,
    "living_area": 20,
    "warehouse_character": 12,
    "light_aspect": 11,
    "pool": 9,
    "parks": 9,
    "restaurants": 9,
}
STRATA_MAX_PENALTY = 10            # soft penalty, subtracted from the weighted sum

# Amenity catchment keys -> OSM tags we accept for each.
# osm_amenities.geojson features carry a "catchment" property naming their class.
CATCHMENT_CLASSES = ("transport", "supermarket", "pool", "park", "restaurant")

LIGHT_TOKENS = re.compile(
    r"\b(light[- ]?filled|sun[- ]?(?:drenched|filled|soaked)|abundant (?:natural )?light|"
    r"north[- ]?(?:facing|east|aspect)|northerly|bright|airy|sunny|sun-?lit|"
    r"floor[- ]to[- ]ceiling|wall[s]? of glass|natural light)\b",
    re.I,
)
WAREHOUSE_TOKENS = re.compile(
    r"\b(warehouse|conversion|converted|loft|exposed brick|exposed steel|sawtooth|"
    r"high ceilings?|soaring ceilings?|industrial (?:heritage|character|chic))\b",
    re.I,
)

# Outlook detection patterns - order matters (water > park > elevated > leafy > city)
OUTLOOK_PATTERNS = [
    ("water", re.compile(
        r"\b(water\s*views?|harbour\s*(?:views?|bridge)|bridge\s*views?|ocean\s*views?|"
        r"bay\s*views?|river\s*views?|waterfront|harbourside|waterside|"
        r"sweeping\s*(?:water|harbour|ocean|bay)|panoramic\s*(?:water|harbour))\b", re.I)),
    ("park", re.compile(
        r"\b(park\s*views?|parkland\s*views?|overlook(?:s|ing)?\s*(?:the\s*)?park|"
        r"green\s*views?|parkside|facing\s*(?:the\s*)?park)\b", re.I)),
    ("elevated_district", re.compile(
        r"\b(district\s*views?|sweeping\s*views?|panoramic\s*views?|"
        r"elevated\s*(?:views?|position)|commanding\s*views?|uninterrupted\s*views?|"
        r"breathtaking\s*views?|spectacular\s*views?)\b", re.I)),
    ("leafy", re.compile(
        r"\b(leafy\s*(?:outlook|views?|street)?|tree[- ]?lined|garden\s*views?|"
        r"green\s*outlook|treetop\s*views?|private\s*(?:leafy|green))\b", re.I)),
    ("city", re.compile(
        r"\b(city\s*(?:views?|skyline|glimpses?)|skyline\s*views?|urban\s*views?|"
        r"CBD\s*views?|city\s*lights?)\b", re.I)),
]


def detect_outlook_from_text(listing):
    """Auto-detect outlook class from description text."""
    desc = listing.get("description", "") or ""
    # Also check any outlook basis text that might have been manually entered
    outlook_basis = (listing.get("outlook") or {}).get("basis", "") or ""
    text = f"{desc} {outlook_basis}".lower()

    for outlook_class, pattern in OUTLOOK_PATTERNS:
        if pattern.search(text):
            return outlook_class
    return None


# ---------------------------------------------------------------------------
# Geometry - Euclidean (great-circle) 1,500 m test  (Adam Q3 = b)
# ---------------------------------------------------------------------------

def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_amenities(geojson_path):
    """Return {class: [(lat, lon), ...]} from osm_amenities.geojson."""
    with open(geojson_path, "r", encoding="utf-8") as fh:
        gj = json.loads(fh.read())
    buckets = {c: [] for c in CATCHMENT_CLASSES}
    for feat in gj.get("features", []):
        props = feat.get("properties", {})
        cls = props.get("catchment")
        if cls not in buckets:
            continue
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        if not coords:
            continue
        lon, lat = coords[0], coords[1]   # GeoJSON is [lon, lat]
        buckets[cls].append((lat, lon))
    return buckets


def compute_catchments(listing, amenities):
    """Set listing['catchments'] = {bool-or-None per class, nearest_m per class}.

    A class is None (unknown) when there is no cached amenity data for it, so an
    empty/absent osm cache flags Tier 1 transport/supplies for manual check
    rather than silently failing every listing. Run build_osm_cache.py to make
    these real True/False verdicts.
    """
    lat, lon = listing.get("lat"), listing.get("lon")
    result = {"transport": None, "supermarket": None, "pool": None,
              "park": None, "restaurants": None, "nearest_m": {}}
    if lat is None or lon is None:
        result["geocoded"] = False
        listing["catchments"] = result
        return result
    result["geocoded"] = True
    name_map = {"transport": "transport", "supermarket": "supermarket",
                "pool": "pool", "park": "park", "restaurants": "restaurant"}
    for out_key, osm_key in name_map.items():
        pts = amenities.get(osm_key, [])
        if not pts:
            result[out_key] = None
            continue
        nearest = None
        for (alat, alon) in pts:
            d = haversine_m(lat, lon, alat, alon)
            if nearest is None or d < nearest:
                nearest = d
        result["nearest_m"][out_key] = round(nearest)
        result[out_key] = nearest <= CATCHMENT_M
    listing["catchments"] = result
    return result


# ---------------------------------------------------------------------------
# Tier 1 - hard parameters (boolean pass/fail)
# ---------------------------------------------------------------------------

def tier1(listing):
    """
    Returns the tier1 dict. Each criterion is True (pass), False (fail) or
    None (cannot determine from listing data -> needs manual check, NOT a fail).
    listing['tier1']['pass'] is True only if no determinable criterion fails.
    """
    c = {}

    # Budget - use the lower bound of the guide if a range is given.
    pmin = listing.get("price_min")
    pmax = listing.get("price_max")
    guide = pmin if pmin is not None else pmax
    c["budget"] = (guide is not None and guide <= BUDGET_CEILING) if guide is not None else None

    # Property type (Adam Q2): apartment, OR 2BR brick/stone cottage, OR
    # warehouse-conversion apartment meeting Tier 1. Raw shells excluded.
    ptype = (listing.get("property_type") or "").lower()
    is_shell = bool(listing.get("is_raw_shell"))
    if is_shell:
        c["property_type"] = False
    elif ptype in ("apartment", "unit", "flat", "warehouse_conversion", "townhouse"):
        c["property_type"] = True
    elif ptype in ("house", "cottage", "semi", "duplex"):
        beds = listing.get("beds")
        c["property_type"] = (beds is not None and beds <= 2)
    elif ptype == "":
        c["property_type"] = None
    else:
        c["property_type"] = None

    # Accessibility - step-free + lift for apartments. Rarely stated in a
    # listing; default to None (unverified) unless flags are explicitly set.
    acc = listing.get("accessibility")  # expected: True / False / None / dict
    if isinstance(acc, dict):
        step_free = acc.get("step_free")
        lift = acc.get("lift")
        if step_free is False or (ptype in ("apartment", "unit", "flat",
                                            "warehouse_conversion") and lift is False):
            c["accessibility"] = False
        elif step_free is True and (ptype not in ("apartment", "unit", "flat",
                                                  "warehouse_conversion") or lift is True):
            c["accessibility"] = True
        else:
            c["accessibility"] = None
    else:
        c["accessibility"] = acc if acc in (True, False) else None

    # Bedrooms - apartments >=2, cottages 2.
    beds = listing.get("beds")
    if beds is None:
        c["bedrooms"] = None
    elif ptype in ("house", "cottage", "semi", "duplex"):
        c["bedrooms"] = beds == 2
    else:
        c["bedrooms"] = beds >= 2

    # Public transport - within 1,500 m of station/light rail/strong bus.
    cat = listing.get("catchments", {})
    c["transport"] = cat.get("transport")

    # Daily supplies - supermarket/corner shop in or very near.
    c["supplies"] = cat.get("supermarket")

    # Location - in target area. Sweep should only harvest in-area suburbs, so
    # default True unless explicitly flagged out of area.
    in_area = listing.get("in_target_area")
    c["location"] = False if in_area is False else True

    # Zoning - only relevant to warehouse-character stock. E1/E2/MU1 pass;
    # E3/E4 fail (decision #17). n/a for ordinary residential stock.
    z = listing.get("zoning") or {}
    if listing.get("warehouse_character") or z.get("checked"):
        verdict = z.get("verdict")
        if verdict == "pass":
            c["zoning"] = True
        elif verdict == "fail":
            c["zoning"] = False
        else:
            c["zoning"] = None
    else:
        c["zoning"] = "n/a"

    fails = [k for k, v in c.items() if v is False]
    unverified = [k for k, v in c.items() if v is None]
    c["fails"] = fails
    c["unverified"] = unverified
    c["pass"] = len(fails) == 0
    return c


# ---------------------------------------------------------------------------
# Tier 2 - weighted discriminators (0-100)
# ---------------------------------------------------------------------------

def _living_area_factor(m2):
    if m2 is None:
        return None
    if m2 >= 150:
        return 1.0
    if m2 >= LIVING_AREA_TARGET_M2:        # 115-150 -> 0.80-1.00
        return 0.80 + 0.20 * (m2 - 115) / (150 - 115)
    if m2 >= 92:                            # 92-115 -> 0.40-0.80 (survey floor)
        return 0.40 + 0.40 * (m2 - 92) / (115 - 92)
    return max(0.15, 0.40 * m2 / 92)


def _light_factor(listing):
    la = listing.get("light_aspect")
    if isinstance(la, (int, float)):
        return max(0.0, min(1.0, float(la)))
    desc = listing.get("description") or ""
    hits = len(set(m.group(0).lower() for m in LIGHT_TOKENS.finditer(desc)))
    if hits == 0:
        return 0.3
    return min(1.0, 0.4 + 0.2 * hits)


def detect_warehouse_character(listing):
    """Heuristic flag from description tokens; sweep may override explicitly."""
    if "warehouse_character" in listing and listing["warehouse_character"] is not None:
        return bool(listing["warehouse_character"])
    desc = listing.get("description") or ""
    return bool(WAREHOUSE_TOKENS.search(desc))


def tier2(listing, t1):
    comp = {}
    # Outlook (leading) - auto-detect from description if not manually set
    oc = (listing.get("outlook") or {}).get("class")
    if not oc or oc == "none":
        detected = detect_outlook_from_text(listing)
        if detected:
            oc = detected
            # Update the listing with detected outlook
            if "outlook" not in listing:
                listing["outlook"] = {}
            listing["outlook"]["class"] = detected
            listing["outlook"]["basis"] = f"Auto-detected from description"
    o_factor = OUTLOOK_SCORE.get(oc, OUTLOOK_SCORE[None])
    comp["outlook"] = o_factor * WEIGHTS["outlook"]

    # Living-area scale
    la_factor = _living_area_factor(listing.get("internal_m2"))
    comp["living_area"] = (la_factor if la_factor is not None else 0.4) * WEIGHTS["living_area"]

    # Warehouse-conversion character - only credited where Tier 1 accessibility
    # is not failed (decision #17: single-level, lifted buildings only).
    wc = detect_warehouse_character(listing)
    acc_failed = t1.get("accessibility") is False
    comp["warehouse_character"] = (WEIGHTS["warehouse_character"]
                                   if (wc and not acc_failed) else 0)

    # Light & aspect
    comp["light_aspect"] = _light_factor(listing) * WEIGHTS["light_aspect"]

    # Catchment-based discriminators (unknown -> 0 credit, never negative)
    cat = listing.get("catchments", {})
    comp["pool"] = WEIGHTS["pool"] if cat.get("pool") else 0
    comp["parks"] = WEIGHTS["parks"] if cat.get("park") else 0
    comp["restaurants"] = WEIGHTS["restaurants"] if cat.get("restaurants") else 0

    raw = sum(comp.values())

    # Soft strata penalty (further-check-A): only above the ~$12k baseline.
    strata = listing.get("strata_pa")
    penalty = 0.0
    if isinstance(strata, (int, float)) and strata > STRATA_BASELINE_PA:
        penalty = min(STRATA_MAX_PENALTY,
                      STRATA_MAX_PENALTY * (strata - STRATA_BASELINE_PA) / 8000.0)
    score = max(0.0, min(100.0, raw - penalty))

    leading_key = max(comp, key=comp.get)
    leading_label = {
        "outlook": "outlook: " + str(oc or "unknown"),
        "living_area": "living-area scale",
        "warehouse_character": "warehouse-conversion character",
        "light_aspect": "light & aspect",
        "pool": "pool within 1.5 km",
        "parks": "parks within 1.5 km",
        "restaurants": "restaurants within 1.5 km",
    }[leading_key]

    return {
        "score": round(score),
        "leading": leading_label,
        "components": {k: round(v, 1) for k, v in comp.items()},
        "strata_penalty": round(penalty, 1),
        "warehouse_character": wc,
    }


# ---------------------------------------------------------------------------
# Orchestration for one listing
# ---------------------------------------------------------------------------

def score_listing(listing, amenities):
    compute_catchments(listing, amenities)
    t1 = tier1(listing)
    t2 = tier2(listing, t1)
    listing["tier1"] = t1
    listing["tier2"] = t2
    listing["warehouse_character"] = t2["warehouse_character"]
    return listing


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    in_path, osm_path = argv[1], argv[2]
    out_path = argv[3] if len(argv) > 3 else None
    with open(in_path, "r", encoding="utf-8") as fh:
        data = json.loads(fh.read())
    listings = data["listings"] if isinstance(data, dict) and "listings" in data else data
    amenities = load_amenities(osm_path)
    for lst in listings:
        score_listing(lst, amenities)
    out = json.dumps(listings, indent=2, ensure_ascii=False)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(out)
        print("Scored " + str(len(listings)) + " listings -> " + out_path)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
