#!/usr/bin/env python3
"""
geocode.py - add lat/lon coordinates to listings using OpenStreetMap Nominatim.

Nominatim is free but requires:
  - A valid User-Agent identifying the application
  - Max 1 request per second (we use 1.1s delay to be safe)
  - No bulk/commercial use without permission

This script reads a harvest or listings JSON, geocodes any listings missing
lat/lon, and writes the result back (or to a new file).

CLI:
    python geocode.py data/listings.json                    # in-place
    python geocode.py data/harvest.json -o data/enriched.json
    python geocode.py data/listings.json --dry-run          # preview only
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "SydneyPropertyDashboard/1.0 (personal use; adam@adam.taylor.name)"
REQUEST_DELAY = 1.1  # seconds between requests (Nominatim requires <=1/sec)


def geocode_address(address: str, suburb: str, postcode: str = None, retry_count: int = 0) -> tuple[float, float] | None:
    """
    Query Nominatim for an address. Returns (lat, lon) or None if not found.
    Handles rate limiting with exponential backoff.
    """
    # Build a full address string
    parts = [address]
    if suburb:
        parts.append(suburb)
    parts.append("NSW")
    parts.append("Australia")
    if postcode:
        parts.insert(-1, postcode)

    query = ", ".join(p for p in parts if p)

    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "au",
    }

    url = NOMINATIM_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429 and retry_count < 3:
            # Rate limited - back off exponentially
            wait = (2 ** retry_count) * 5  # 5s, 10s, 20s
            print(f"rate limited, waiting {wait}s...", end=" ", file=sys.stderr)
            time.sleep(wait)
            return geocode_address(address, suburb, postcode, retry_count + 1)
        print(f"  ERROR geocoding '{query}': {e}", file=sys.stderr)
        return None
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  ERROR geocoding '{query}': {e}", file=sys.stderr)
        return None

    if not data:
        # Try a simpler query with just suburb
        if suburb:
            time.sleep(REQUEST_DELAY)  # Rate limit before retry
            params["q"] = f"{address}, {suburb}, Australia"
            url = NOMINATIM_URL + "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception:
                pass

    if not data:
        return None

    try:
        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
        return (lat, lon)
    except (KeyError, ValueError, IndexError):
        return None


def geocode_listings(listings: list[dict], dry_run: bool = False, max_per_run: int = 20) -> tuple[int, int]:
    """
    Geocode listings that are missing lat/lon.
    Returns (success_count, fail_count).

    Args:
        max_per_run: Limit geocoding to avoid rate limits (0 = no limit)
    """
    need_geocoding = [l for l in listings if l.get("lat") is None or l.get("lon") is None]
    # Filter out listings with no address
    need_geocoding = [l for l in need_geocoding if l.get("address") or l.get("address_text")]

    if not need_geocoding:
        print("All listings already have coordinates.", file=sys.stderr)
        return (0, 0)

    # Limit per run to avoid rate limiting
    if max_per_run and len(need_geocoding) > max_per_run:
        print(f"Geocoding {max_per_run} of {len(need_geocoding)} listings (limited to avoid rate limits)...", file=sys.stderr)
        need_geocoding = need_geocoding[:max_per_run]
    else:
        print(f"Geocoding {len(need_geocoding)} listings (of {len(listings)} total)...", file=sys.stderr)

    success = 0
    fail = 0
    consecutive_fails = 0

    for i, listing in enumerate(need_geocoding, 1):
        address = listing.get("address") or listing.get("address_text", "")
        suburb = listing.get("suburb", "")
        postcode = listing.get("postcode", "")

        display = f"{address}, {suburb}" if suburb else address
        print(f"  [{i}/{len(need_geocoding)}] {display}...", end=" ", file=sys.stderr)

        if dry_run:
            print("(dry run)", file=sys.stderr)
            continue

        coords = geocode_address(address, suburb, postcode)

        if coords:
            listing["lat"] = coords[0]
            listing["lon"] = coords[1]
            print(f"OK ({coords[0]:.5f}, {coords[1]:.5f})", file=sys.stderr)
            success += 1
            consecutive_fails = 0
        else:
            print("NOT FOUND", file=sys.stderr)
            fail += 1
            consecutive_fails += 1

            # Stop if we get too many consecutive failures (likely rate limited)
            if consecutive_fails >= 5:
                print(f"Stopping early - {consecutive_fails} consecutive failures (likely rate limited)", file=sys.stderr)
                break

        # Rate limiting - be nice to Nominatim (1.5s to be safe)
        if i < len(need_geocoding):
            time.sleep(1.5)

    return (success, fail)


def main(argv):
    ap = argparse.ArgumentParser(
        description="Geocode listings using OpenStreetMap Nominatim (free, rate-limited)."
    )
    ap.add_argument("input", help="Input JSON file (harvest or listings.json)")
    ap.add_argument("-o", "--output", help="Output file (default: overwrite input)")
    ap.add_argument("--dry-run", action="store_true", help="Preview without making requests")
    args = ap.parse_args(argv[1:])

    with open(args.input, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    # Handle both harvest format {"listings": [...]} and raw list [...]
    if isinstance(data, list):
        listings = data
        is_wrapped = False
    else:
        listings = data.get("listings", [])
        is_wrapped = True

    if not listings:
        print("No listings found in input file.", file=sys.stderr)
        return 1

    success, fail = geocode_listings(listings, dry_run=args.dry_run)

    if args.dry_run:
        print(f"\nDry run complete. Would geocode {len([l for l in listings if l.get('lat') is None])} listings.", file=sys.stderr)
        return 0

    print(f"\nGeocoded {success} listings, {fail} failed/skipped.", file=sys.stderr)

    # Write output
    out_path = args.output or args.input
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)

    print(f"Wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
