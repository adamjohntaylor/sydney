#!/usr/bin/env python3
"""
geocode.py - add lat/lon coordinates to listings.

Supports two geocoding providers:
  1. Google Maps Geocoding API (preferred - fast, reliable, no rate issues)
  2. OpenStreetMap Nominatim (free fallback - strict rate limits)

Setup for Google Maps:
  1. Go to https://console.cloud.google.com/
  2. Create a project and enable "Geocoding API"
  3. Create an API key (APIs & Services > Credentials)
  4. Add to dashboard/data/.geocode_config.json:
     {"google_api_key": "YOUR_API_KEY"}

If no Google API key is found, falls back to Nominatim (rate-limited).

CLI:
    python geocode.py data/listings.json                    # in-place
    python geocode.py data/harvest.json -o data/enriched.json
    python geocode.py data/listings.json --dry-run          # preview only
    python geocode.py data/listings.json --provider nominatim  # force Nominatim
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

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
CONFIG_PATH = os.path.join(DATA, ".geocode_config.json")

# API endpoints
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim settings
USER_AGENT = "SydneyPropertyDashboard/1.0 (personal use; adam@adam.taylor.name)"
NOMINATIM_DELAY = 1.5  # seconds between requests

# Load config
_config = {}
if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r") as f:
            _config = json.load(f)
    except Exception:
        pass

GOOGLE_API_KEY = _config.get("google_api_key", "")


def geocode_google(address: str, suburb: str, postcode: str = None) -> tuple[float, float] | None:
    """
    Query Google Maps Geocoding API. Returns (lat, lon) or None if not found.
    Fast, reliable, 40k free requests/month.
    """
    # Build address string
    parts = [address]
    if suburb:
        parts.append(suburb)
    if postcode:
        parts.append(postcode)
    parts.append("NSW")
    parts.append("Australia")

    query = ", ".join(p for p in parts if p)

    params = {
        "address": query,
        "key": GOOGLE_API_KEY,
        "region": "au",
    }

    url = GOOGLE_GEOCODE_URL + "?" + urllib.parse.urlencode(params)

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"ERROR (Google): {e}", file=sys.stderr)
        return None

    if data.get("status") != "OK" or not data.get("results"):
        # Try simpler query
        if suburb:
            params["address"] = f"{address}, {suburb}, Australia"
            url = GOOGLE_GEOCODE_URL + "?" + urllib.parse.urlencode(params)
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception:
                pass

    if data.get("status") != "OK" or not data.get("results"):
        return None

    try:
        location = data["results"][0]["geometry"]["location"]
        return (location["lat"], location["lng"])
    except (KeyError, IndexError):
        return None


def geocode_nominatim(address: str, suburb: str, postcode: str = None, retry_count: int = 0) -> tuple[float, float] | None:
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
            return geocode_nominatim(address, suburb, postcode, retry_count + 1)
        print(f"  ERROR geocoding '{query}': {e}", file=sys.stderr)
        return None
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  ERROR geocoding '{query}': {e}", file=sys.stderr)
        return None

    if not data:
        # Try a simpler query with just suburb
        if suburb:
            time.sleep(NOMINATIM_DELAY)  # Rate limit before retry
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


def geocode_address(address: str, suburb: str, postcode: str = None, provider: str = "auto") -> tuple[float, float] | None:
    """
    Geocode an address using the specified provider.

    Args:
        provider: "auto" (Google if key present, else Nominatim), "google", or "nominatim"
    """
    if provider == "auto":
        provider = "google" if GOOGLE_API_KEY else "nominatim"

    if provider == "google":
        if not GOOGLE_API_KEY:
            print("WARNING: No Google API key, falling back to Nominatim", file=sys.stderr)
            return geocode_nominatim(address, suburb, postcode)
        return geocode_google(address, suburb, postcode)
    else:
        return geocode_nominatim(address, suburb, postcode)


def geocode_listings(listings: list[dict], dry_run: bool = False, max_per_run: int = 50, provider: str = "auto") -> tuple[int, int]:
    """
    Geocode listings that are missing lat/lon.
    Returns (success_count, fail_count).

    Args:
        max_per_run: Limit geocoding per run (0 = no limit). Higher for Google, lower for Nominatim.
        provider: "auto", "google", or "nominatim"
    """
    # Determine actual provider
    actual_provider = provider
    if provider == "auto":
        actual_provider = "google" if GOOGLE_API_KEY else "nominatim"

    # Adjust limits based on provider
    if actual_provider == "nominatim" and max_per_run > 20:
        max_per_run = 20  # Nominatim needs lower limits

    need_geocoding = [l for l in listings if l.get("lat") is None or l.get("lon") is None]
    # Filter out listings with no address
    need_geocoding = [l for l in need_geocoding if l.get("address") or l.get("address_text")]

    if not need_geocoding:
        print("All listings already have coordinates.", file=sys.stderr)
        return (0, 0)

    # Limit per run
    total_needing = len(need_geocoding)
    if max_per_run and len(need_geocoding) > max_per_run:
        print(f"Geocoding {max_per_run} of {total_needing} listings via {actual_provider.upper()}...", file=sys.stderr)
        need_geocoding = need_geocoding[:max_per_run]
    else:
        print(f"Geocoding {len(need_geocoding)} listings via {actual_provider.upper()}...", file=sys.stderr)

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

        coords = geocode_address(address, suburb, postcode, provider=actual_provider)

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

            # Stop if we get too many consecutive failures (likely rate limited for Nominatim)
            if actual_provider == "nominatim" and consecutive_fails >= 5:
                print(f"Stopping early - {consecutive_fails} consecutive failures (likely rate limited)", file=sys.stderr)
                break

        # Rate limiting - only needed for Nominatim
        if actual_provider == "nominatim" and i < len(need_geocoding):
            time.sleep(NOMINATIM_DELAY)
        elif actual_provider == "google" and i < len(need_geocoding):
            time.sleep(0.1)  # Small delay to be nice to Google

    return (success, fail)


def main(argv):
    ap = argparse.ArgumentParser(
        description="Geocode listings using Google Maps or Nominatim."
    )
    ap.add_argument("input", help="Input JSON file (harvest or listings.json)")
    ap.add_argument("-o", "--output", help="Output file (default: overwrite input)")
    ap.add_argument("--dry-run", action="store_true", help="Preview without making requests")
    ap.add_argument("--provider", choices=["auto", "google", "nominatim"], default="auto",
                    help="Geocoding provider (default: auto - Google if key present)")
    args = ap.parse_args(argv[1:])

    # Show which provider will be used
    provider = args.provider
    if provider == "auto":
        provider = "google" if GOOGLE_API_KEY else "nominatim"
    print(f"Using {provider.upper()} for geocoding", file=sys.stderr)
    if provider == "google" and not GOOGLE_API_KEY:
        print(f"WARNING: No Google API key found at {CONFIG_PATH}", file=sys.stderr)
        print("Falling back to Nominatim (rate-limited)", file=sys.stderr)

    with open(args.input, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    # Handle both harvest format {"listings": [...]} and raw list [...]
    if isinstance(data, list):
        listings = data
    else:
        listings = data.get("listings", [])

    if not listings:
        print("No listings found in input file.", file=sys.stderr)
        return 1

    success, fail = geocode_listings(listings, dry_run=args.dry_run, provider=args.provider)

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
