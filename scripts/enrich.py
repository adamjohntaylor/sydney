#!/usr/bin/env python3
"""
enrich.py - enrich listings with data from Domain/REA listing URLs.

Since Domain/REA block automated searching, this script works with URLs you provide:
1. Provide a listing URL to add to an existing address
2. Or provide a full listing URL to add as a new listing

Usage:
    python enrich.py --url "https://domain.com.au/..." --address "40 High Street" --suburb "Balmain"
    python enrich.py --url "https://domain.com.au/..."   # add as new listing
    python enrich.py --list                              # show listings needing URLs
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "..")
DATA = os.path.join(DASH, "data")
LISTINGS_PATH = os.path.join(DATA, "listings.json")
OSM_PATH = os.path.join(DATA, "osm_amenities.geojson")

REQUEST_DELAY = 2.0  # Be nice to Domain


def _rescore_all(data):
    """Re-score every listing in `data` in place, so a CLI enrichment refreshes
    the Tier 1/Tier 2 marks the same way the dashboard server's enrich path does.
    Pure/local; falls back to empty amenities if the OSM cache isn't built."""
    try:
        import score as score_mod
    except Exception:
        return  # scoring optional; never block a save
    listings = data.get("listings", [])
    if os.path.exists(OSM_PATH):
        amenities = score_mod.load_amenities(OSM_PATH)
    else:
        amenities = {c: [] for c in score_mod.CATCHMENT_CLASSES}
    for l in listings:
        score_mod.score_listing(l, amenities)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def search_domain(address: str, suburb: str) -> dict | None:
    """Search Domain for a listing by address and return details if found."""

    # Clean up address for search
    address_clean = re.sub(r'[^\w\s]', ' ', address).strip()
    suburb_clean = suburb.strip()

    # Build search URL - search by address text
    query = f"{address_clean} {suburb_clean}"
    search_url = f"https://www.domain.com.au/sale/?searchterm={urllib.parse.quote(query)}"

    try:
        req = urllib.request.Request(search_url)
        for k, v in HEADERS.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"    Search failed: {e}", file=sys.stderr)
        return None

    # Try to find listing URL in search results
    # Domain listing URLs look like: /address-suburb-state-postcode-12345678
    listing_pattern = re.compile(
        rf'href="(https://www\.domain\.com\.au/[^"]*?{re.escape(suburb_clean.lower().replace(" ", "-"))}[^"]*?-(\d{{7,12}}))"',
        re.I
    )

    match = listing_pattern.search(html)
    if not match:
        # Try a looser pattern
        listing_pattern2 = re.compile(r'href="(https://www\.domain\.com\.au/[a-z0-9\-]+-(\d{7,12}))"', re.I)
        match = listing_pattern2.search(html)

    if not match:
        return None

    listing_url = match.group(1)

    # Now fetch the listing page for details
    return fetch_listing_details(listing_url)


def fetch_listing_details(url: str) -> dict | None:
    """Fetch a Domain listing page and extract details."""
    try:
        req = urllib.request.Request(url)
        for k, v in HEADERS.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"    Fetch failed: {e}", file=sys.stderr)
        return None

    details = {"url": url}

    # Extract cover image
    img_match = re.search(r'"(https://rimh2\.domainstatic\.com\.au/[^"]+)"', html)
    if img_match:
        details["cover_image"] = img_match.group(1)

    # Extract beds/baths/parking from structured data or page content
    beds_match = re.search(r'"bedrooms"\s*:\s*(\d+)', html) or re.search(r'>(\d+)\s*<[^>]*>\s*Beds?', html, re.I)
    if beds_match:
        details["beds"] = int(beds_match.group(1))

    baths_match = re.search(r'"bathrooms"\s*:\s*(\d+)', html) or re.search(r'>(\d+)\s*<[^>]*>\s*Baths?', html, re.I)
    if baths_match:
        details["baths"] = int(baths_match.group(1))

    parking_match = re.search(r'"carspaces"\s*:\s*(\d+)', html) or re.search(r'>(\d+)\s*<[^>]*>\s*Parking', html, re.I)
    if parking_match:
        details["parking"] = int(parking_match.group(1))

    # Extract property type
    type_match = re.search(r'"propertyType"\s*:\s*"([^"]+)"', html)
    if type_match:
        details["property_type"] = type_match.group(1).lower()

    # Extract internal area
    area_match = re.search(r'(\d+)\s*m²\s*(?:internal|floor|living)', html, re.I)
    if area_match:
        details["internal_m2"] = int(area_match.group(1))

    # Extract description
    desc_match = re.search(r'"description"\s*:\s*"([^"]{50,500})', html)
    if desc_match:
        desc = desc_match.group(1)
        desc = desc.encode().decode('unicode_escape')  # Handle \n etc
        details["description"] = desc[:500]

    # Extract lat/lon if available
    lat_match = re.search(r'"latitude"\s*:\s*([\-\d.]+)', html)
    lon_match = re.search(r'"longitude"\s*:\s*([\d.]+)', html)
    if lat_match and lon_match:
        details["lat"] = float(lat_match.group(1))
        details["lon"] = float(lon_match.group(1))

    # Extract open homes
    # Look for inspection times in the page
    open_homes = []
    oh_pattern = re.compile(r'"inspectionTime"\s*:\s*"([^"]+)"')
    for m in oh_pattern.finditer(html):
        open_homes.append(m.group(1))
    if open_homes:
        details["open_homes"] = open_homes[:5]  # Limit to 5

    return details


def enrich_listings(dry_run=False, limit=None):
    """Find and enrich listings missing URLs."""

    with open(LISTINGS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    listings = data.get("listings", [])

    # Find listings without URLs (came from email)
    needs_enrichment = [l for l in listings if not l.get("url") and l.get("address")]

    if not needs_enrichment:
        print("No listings need enrichment.", file=sys.stderr)
        return 0

    if limit:
        needs_enrichment = needs_enrichment[:limit]

    print(f"Enriching {len(needs_enrichment)} listings...", file=sys.stderr)

    enriched_count = 0
    for i, listing in enumerate(needs_enrichment, 1):
        address = listing.get("address", "")
        suburb = listing.get("suburb", "")

        print(f"  [{i}/{len(needs_enrichment)}] {address}, {suburb}...", end=" ", file=sys.stderr)

        if dry_run:
            print("(dry run)", file=sys.stderr)
            continue

        details = search_domain(address, suburb)

        if details:
            # Update listing with new details
            for key, value in details.items():
                if value is not None and (key not in listing or listing[key] is None):
                    listing[key] = value
            print(f"OK - {details.get('url', 'no url')[:60]}", file=sys.stderr)
            enriched_count += 1
        else:
            print("NOT FOUND", file=sys.stderr)

        time.sleep(REQUEST_DELAY)

    if not dry_run and enriched_count > 0:
        _rescore_all(data)
        with open(LISTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\nEnriched {enriched_count} listings.", file=sys.stderr)

    return enriched_count


def list_needing_urls():
    """Show listings that don't have URLs."""
    with open(LISTINGS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    listings = data.get("listings", [])
    needs_url = [l for l in listings if not l.get("url") and l.get("address")]

    if not needs_url:
        print("All listings have URLs.", file=sys.stderr)
        return

    print(f"\n{len(needs_url)} listings need URLs:\n")
    for i, l in enumerate(needs_url, 1):
        addr = l.get("address", "?")
        suburb = l.get("suburb", "?")
        price = l.get("price_guide_text", "")
        print(f"  {i}. {addr}, {suburb} {price}")


def add_url_to_listing(url: str, address: str = None, suburb: str = None):
    """Add a URL to an existing listing (Domain blocks auto-fetching)."""
    with open(LISTINGS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    listings = data.get("listings", [])

    # Find matching listing by address/suburb
    target = None
    if address and suburb:
        addr_lower = address.lower().strip()
        suburb_lower = suburb.lower().strip()
        for l in listings:
            if (addr_lower in l.get("address", "").lower() and
                suburb_lower == l.get("suburb", "").lower()):
                target = l
                break

    if not target:
        # Try fuzzy match on address only
        if address:
            addr_lower = address.lower().strip()
            for l in listings:
                if addr_lower in l.get("address", "").lower() and not l.get("url"):
                    target = l
                    break

    if target:
        target["url"] = url
        # Extract source from URL
        if "domain.com.au" in url:
            target["source"] = "domain"
        elif "realestate.com.au" in url:
            target["source"] = "realestate"
        print(f"Added URL to: {target.get('address')}, {target.get('suburb')}", file=sys.stderr)
    else:
        print(f"No matching listing found for '{address}, {suburb}'", file=sys.stderr)
        print("Available listings without URLs:", file=sys.stderr)
        for l in listings:
            if not l.get("url"):
                print(f"  - {l.get('address')}, {l.get('suburb')}", file=sys.stderr)
        return False

    # Re-score so Tier 1/Tier 2 marks stay consistent with the server path.
    _rescore_all(data)

    # Save
    with open(LISTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return True


def main(argv):
    ap = argparse.ArgumentParser(description="Enrich listings with Domain/REA URLs.")
    ap.add_argument("--url", help="Domain or REA listing URL to add")
    ap.add_argument("--address", help="Address to match (e.g., '40 High Street')")
    ap.add_argument("--suburb", help="Suburb to match (e.g., 'Balmain')")
    ap.add_argument("--list", action="store_true", help="List listings needing URLs")
    args = ap.parse_args(argv[1:])

    if args.list:
        list_needing_urls()
        return 0

    if args.url:
        success = add_url_to_listing(args.url, args.address, args.suburb)
        return 0 if success else 1

    # Default: show listings needing URLs
    list_needing_urls()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
