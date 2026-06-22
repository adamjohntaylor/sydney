#!/usr/bin/env python3
"""
build_osm_cache.py - build/refresh data/osm_amenities.geojson from OpenStreetMap.

Walkability (Adam Q3 = Euclidean 1,500 m) is computed by score.py against a
cached set of amenity points. This script fetches that set from the Overpass API
for the target-area bounding box and writes a flat GeoJSON of points, each tagged
with a "catchment" class in {transport, supermarket, pool, park, restaurant}.

Run it once to enable walkability scoring, then monthly to refresh:
    cd D:\\Projects\\Sydney\\dashboard
    python scripts\\build_osm_cache.py

Requires network access to overpass-api.de (the Cowork sandbox does NOT have it,
so run this on your own machine - Python is already installed per serve.py).
Only dependency is `requests` (pip install requests) - falls back to urllib.

Target area (decision #15): Inner West from Zetland through Dulwich Hill, plus
Drummoyne north of Victoria Road. Bounding box below comfortably covers it.
"""

from __future__ import annotations
import json
import os
import sys
import time

# S, W, N, E
BBOX = (-33.935, 151.115, -33.835, 151.225)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "osm_amenities.geojson")
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# OSM selectors per catchment class. Bus stops are included for transport because
# the Drummoyne limb meets the transport test via the Victoria Road bus corridor
# and the F3 ferry (decision #15), not rail.
QUERIES = {
    "transport": [
        'node["railway"="station"]', 'node["railway"="tram_stop"]',
        'node["station"="light_rail"]', 'node["amenity"="ferry_terminal"]',
        'node["highway"="bus_stop"]',
    ],
    "supermarket": ['node["shop"="supermarket"]', 'way["shop"="supermarket"]',
                    'node["shop"="convenience"]'],
    "pool": ['node["leisure"="swimming_pool"]["access"!="private"]',
             'way["leisure"="swimming_pool"]["access"!="private"]',
             'way["leisure"="sports_centre"]["sport"="swimming"]'],
    "park": ['way["leisure"="park"]', 'node["leisure"="park"]'],
    "restaurant": ['node["amenity"="restaurant"]', 'node["amenity"="cafe"]'],
}


def build_query(selectors):
    s, w, n, e = BBOX
    bbox = f"({s},{w},{n},{e})"
    body = "".join(f"{sel}{bbox};" for sel in selectors)
    return f"[out:json][timeout:120];({body});out center;"


def run_overpass(query):
    try:
        import requests
        for ep in ENDPOINTS:
            try:
                r = requests.post(ep, data={"data": query}, timeout=180)
                if r.status_code == 200:
                    return r.json()
            except Exception as exc:  # noqa: BLE001
                print(f"  {ep} failed: {exc}", file=sys.stderr)
    except ImportError:
        import urllib.request, urllib.parse
        data = urllib.parse.urlencode({"data": query}).encode()
        for ep in ENDPOINTS:
            try:
                with urllib.request.urlopen(ep, data=data, timeout=180) as resp:
                    return json.loads(resp.read().decode())
            except Exception as exc:  # noqa: BLE001
                print(f"  {ep} failed: {exc}", file=sys.stderr)
    return None


def to_features(cls, overpass_json):
    feats = []
    for el in overpass_json.get("elements", []):
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        feats.append({
            "type": "Feature",
            "properties": {"catchment": cls,
                           "name": (el.get("tags") or {}).get("name", ""),
                           "osm_id": f"{el.get('type')}/{el.get('id')}"},
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        })
    return feats


def main():
    all_feats = []
    summary = {}
    for cls, selectors in QUERIES.items():
        print(f"Fetching {cls}…")
        res = run_overpass(build_query(selectors))
        if res is None:
            print(f"  ! {cls}: no response (network blocked?)", file=sys.stderr)
            continue
        fs = to_features(cls, res)
        all_feats.extend(fs)
        summary[cls] = len(fs)
        print(f"  {cls}: {len(fs)} features")
        time.sleep(2)   # be polite to Overpass
    gj = {
        "type": "FeatureCollection",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bbox": list(BBOX),
        "summary": summary,
        "features": all_feats,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(gj, fh, ensure_ascii=False)
    print(f"\nWrote {os.path.normpath(OUT)}  ({len(all_feats)} features)")
    if not all_feats:
        print("WARNING: no features fetched. Walkability will stay 'unknown' until "
              "this runs successfully with network access to overpass-api.de.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
