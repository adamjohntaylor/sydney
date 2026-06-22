"""
zoning.py - NSW land-zoning verification for warehouse-character listings.

Standing rule (decision #17; CLAUDE.md): warehouse / industrial-character stock
must be verified on the NSW Planning Portal before it can pass Tier 1.
  - E1 / E2 / MU1  -> lawful for residential -> PASS
  - E3 / E4        -> residential prohibited (IWLEP 2022 cl 6.13) -> FAIL
Ordinary residential zones (R1/R2/R3/R4) are lawful dwellings and pass.

NETWORK NOTE: the dashboard sandbox has no route to the NSW Planning Portal, so
this module does NOT fetch. At sweep time Claude fetches the zoning JSON for a
listing's lat/lon (via Claude-in-Chrome or web_fetch using the URL from
build_query_url) and passes the response text to parse_zoning(). The Spatial
Viewer remains the human source of truth - see verdict()['source_url'].

Primary source: NSW DPE ArcGIS "EPI Primary Planning Layers" Land Zoning layer.
A point query returns the zone code (SYM_CODE) for the parcel under a lat/lon.
"""

from __future__ import annotations
import json
import sys

# ArcGIS REST point-query template. Layer 19 = Land Zoning on the EPI Primary
# Planning Layers MapServer (confirm the layer index at sweep time - ArcGIS
# layer ordering occasionally changes; the field of interest is SYM_CODE / ZONE).
ARCGIS_TEMPLATE = (
    "https://mapprod3.environment.nsw.gov.au/arcgis/rest/services/Planning/"
    "EPI_Primary_Planning_Layers/MapServer/19/query"
    "?geometry={lon},{lat}&geometryType=esriGeometryPoint&inSR=4326"
    "&spatialRel=esriSpatialRelIntersects&outFields=*&returnGeometry=false&f=json"
)

# Human-facing Spatial Viewer (the documented source of truth) centred on a point.
SPATIAL_VIEWER_TEMPLATE = (
    "https://www.planningportal.nsw.gov.au/spatialviewer/#/find-a-property/"
    "address?lat={lat}&lng={lon}"
)

PASS_ZONES = {"E1", "E2", "MU1", "R1", "R2", "R3", "R4", "B4"}   # residential-capable
FAIL_ZONES = {"E3", "E4", "IN1", "IN2", "IN3", "W1", "W2", "W3"}  # residential prohibited


def build_query_url(lat, lon):
    return ARCGIS_TEMPLATE.format(lat=lat, lon=lon)


def spatial_viewer_url(lat, lon):
    return SPATIAL_VIEWER_TEMPLATE.format(lat=lat, lon=lon)


def _extract_zone_code(arcgis_json):
    """Pull the zone symbol code out of an ArcGIS query response (dict or str)."""
    if isinstance(arcgis_json, str):
        arcgis_json = json.loads(arcgis_json)
    feats = arcgis_json.get("features") or []
    if not feats:
        return None
    attrs = feats[0].get("attributes", {})
    for key in ("SYM_CODE", "ZONE", "ZONECODE", "EPI_ZONE", "LAY_CLASS", "ZONE_CODE"):
        val = attrs.get(key)
        if val:
            # SYM_CODE is like "E3"; some layers return "E3: Productivity Support"
            return str(val).split(":")[0].strip().upper()
    return None


def verdict_for_code(code):
    if not code:
        return "unknown"
    code = code.upper().strip()
    if code in PASS_ZONES:
        return "pass"
    if code in FAIL_ZONES:
        return "fail"
    # Unmapped code: be conservative - flag for manual confirmation.
    return "unknown"


def parse_zoning(arcgis_json, lat=None, lon=None):
    """
    Convert a fetched ArcGIS response into the listing['zoning'] dict.
    Returns: {code, verdict, checked, source_url, viewer_url}
    """
    code = _extract_zone_code(arcgis_json)
    return {
        "code": code,
        "verdict": verdict_for_code(code),
        "checked": True,
        "source_url": build_query_url(lat, lon) if lat is not None else None,
        "viewer_url": spatial_viewer_url(lat, lon) if lat is not None else None,
    }


def unverified(lat=None, lon=None):
    """Placeholder zoning record for a warehouse listing not yet checked."""
    return {
        "code": None,
        "verdict": "unknown",
        "checked": False,
        "source_url": build_query_url(lat, lon) if lat is not None else None,
        "viewer_url": spatial_viewer_url(lat, lon) if lat is not None else None,
    }


def main(argv):
    # CLI helpers:
    #   python zoning.py url <lat> <lon>        -> print the ArcGIS query URL
    #   python zoning.py parse <lat> <lon> <response.json>
    if len(argv) >= 4 and argv[1] == "url":
        print(build_query_url(float(argv[2]), float(argv[3])))
        return 0
    if len(argv) >= 5 and argv[1] == "parse":
        lat, lon = float(argv[2]), float(argv[3])
        with open(argv[4], "r", encoding="utf-8") as fh:
            print(json.dumps(parse_zoning(fh.read(), lat, lon), indent=2))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
