#!/usr/bin/env python3
"""
gmail_fetch.py - fetch Domain/REA property alert emails from Gmail and ingest them.

Supports two authentication methods:
  1. IMAP with App Password (recommended - simpler setup)
  2. OAuth2 via Google API (requires Google Cloud project)

Setup for IMAP (recommended):
  1. Enable 2FA on your Google account: https://myaccount.google.com/security
  2. Create an App Password: https://myaccount.google.com/apppasswords
     - Select "Mail" and your device, click Generate
     - Copy the 16-character password
  3. Create dashboard/data/.gmail_credentials.json:
     {"email": "you@gmail.com", "app_password": "xxxx xxxx xxxx xxxx"}

Setup for OAuth (alternative):
  1. Create a project at https://console.cloud.google.com/
  2. Enable Gmail API
  3. Create OAuth credentials (Desktop app)
  4. Download credentials.json to dashboard/data/.gmail_oauth.json
  5. pip install google-auth-oauthlib google-api-python-client

Usage:
    python gmail_fetch.py                    # fetch new alerts, merge into listings
    python gmail_fetch.py --days 7           # look back 7 days (default: 3)
    python gmail_fetch.py --dry-run          # show what would be fetched, don't write
    python gmail_fetch.py --method imap      # force IMAP method
    python gmail_fetch.py --method oauth     # force OAuth method

The script:
  1. Fetches emails from Domain/REA matching saved-search alert patterns
  2. Parses them for listing URLs + basic info (via parse_alert_email logic)
  3. Geocodes any new listings
  4. Merges into listings.json with scoring
"""

from __future__ import annotations
import argparse
import base64
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "..")
DATA = os.path.join(DASH, "data")
if HERE not in sys.path:
    sys.path.insert(0, HERE)   # sibling imports (parse_alert_email, sweep, ...)
IMAP_CREDS_PATH = os.path.join(DATA, ".gmail_credentials.json")
OAUTH_CREDS_PATH = os.path.join(DATA, ".gmail_oauth.json")
TOKEN_PATH = os.path.join(DATA, ".gmail_token.json")
LISTINGS_PATH = os.path.join(DATA, "listings.json")
OSM_PATH = os.path.join(DATA, "osm_amenities.geojson")
ACCESS_CONFIG_PATH = os.path.join(DATA, "accessibility_config.json")


def rea_filter_enabled():
    """True if the REA saved search carries the step-free + elevator accessibility
    filters (Adam sets this in data/accessibility_config.json once he's added them).
    When true, REA-sourced alert listings are tagged accessibility_source='rea_filter'
    so score.py treats them as a PROVISIONAL accessibility pass."""
    try:
        with open(ACCESS_CONFIG_PATH, "r", encoding="utf-8") as fh:
            return bool(json.load(fh).get("rea_search_has_accessibility_filter"))
    except (ValueError, OSError):
        return False

# Gmail API scopes - read-only access to emails
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Alert senders to search for
ALERT_SENDERS = ["noreply@domain.com.au", "noreply@realestate.com.au",
                 "alerts@domain.com.au", "alerts@realestate.com.au",
                 "email@campaign.realestate.com.au"]


def fetch_via_imap(days_back=3):
    """Fetch property alert emails via IMAP with App Password."""
    import imaplib
    import email
    from email.header import decode_header

    # Load credentials
    if not os.path.exists(IMAP_CREDS_PATH):
        print(f"IMAP credentials not found at {IMAP_CREDS_PATH}", file=sys.stderr)
        print("Create this file with: {\"email\": \"you@gmail.com\", \"app_password\": \"xxxx xxxx xxxx xxxx\"}", file=sys.stderr)
        print("\nTo get an App Password:", file=sys.stderr)
        print("1. Enable 2FA: https://myaccount.google.com/security", file=sys.stderr)
        print("2. Create App Password: https://myaccount.google.com/apppasswords", file=sys.stderr)
        return None

    with open(IMAP_CREDS_PATH, "r") as f:
        creds = json.load(f)

    email_addr = creds.get("email")
    app_password = creds.get("app_password", "").replace(" ", "")

    print(f"Connecting to Gmail IMAP as {email_addr}...", file=sys.stderr)

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(email_addr, app_password)
    except imaplib.IMAP4.error as e:
        print(f"IMAP login failed: {e}", file=sys.stderr)
        print("Check your email and app password in .gmail_credentials.json", file=sys.stderr)
        return None

    mail.select("inbox")

    # Search for emails from alert senders in the date range
    since_date = (dt.datetime.now() - dt.timedelta(days=days_back)).strftime("%d-%b-%Y")

    emails = []
    for sender in ALERT_SENDERS:
        search_query = f'(FROM "{sender}" SINCE {since_date})'
        print(f"Searching: {search_query}", file=sys.stderr)

        _, message_numbers = mail.search(None, search_query)
        msg_nums = message_numbers[0].split()

        for num in msg_nums:
            _, msg_data = mail.fetch(num, "(RFC822)")
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            # Get subject
            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8", errors="replace")

            # Get body (HTML preferred)
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        payload = part.get_payload(decode=True)
                        body = payload.decode("utf-8", errors="replace")
                        break
                    elif part.get_content_type() == "text/plain" and not body:
                        payload = part.get_payload(decode=True)
                        body = payload.decode("utf-8", errors="replace")
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")

            if body:
                emails.append({
                    "subject": subject,
                    "date": msg["Date"],
                    "body": body,
                    "from": sender
                })

    mail.logout()
    print(f"Found {len(emails)} alert emails via IMAP", file=sys.stderr)
    return emails


def get_gmail_service(force_reauth=False):
    """Authenticate and return Gmail API service."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("Missing dependencies. Install with:", file=sys.stderr)
        print("  pip install google-auth-oauthlib google-api-python-client", file=sys.stderr)
        sys.exit(1)

    creds = None

    # Load existing token
    if os.path.exists(TOKEN_PATH) and not force_reauth:
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except Exception:
            pass

    # Refresh or get new credentials
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            # Need to authenticate via browser
            # Use OAuth out-of-band flow for installed apps
            flow = InstalledAppFlow.from_client_config(
                {
                    "installed": {
                        "client_id": "292084806032-aeh09k1pf02k1dqkrv1n3s5v5t8l4dup.apps.googleusercontent.com",
                        "project_id": "sydney-dashboard-oauth",
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                        "client_secret": "GOCSPX-placeholder-replace-with-real",
                        "redirect_uris": ["http://localhost"]
                    }
                },
                SCOPES
            )
            print("\nOpening browser for Gmail authorization...", file=sys.stderr)
            print("(If browser doesn't open, check the URL printed below)\n", file=sys.stderr)
            creds = flow.run_local_server(port=0)

        # Save credentials for next run
        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def fetch_alert_emails(service, days_back=3):
    """Fetch property alert emails from the last N days."""
    after_date = (dt.datetime.now() - dt.timedelta(days=days_back)).strftime("%Y/%m/%d")
    query = f"{ALERT_QUERY} after:{after_date}"

    print(f"Searching Gmail: {query}", file=sys.stderr)

    results = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
    messages = results.get("messages", [])

    print(f"Found {len(messages)} alert emails", file=sys.stderr)

    emails = []
    for msg in messages:
        msg_data = service.users().messages().get(userId="me", id=msg["id"], format="full").execute()

        # Get subject and date
        headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
        subject = headers.get("Subject", "")
        date = headers.get("Date", "")

        # Get body
        body = ""
        payload = msg_data.get("payload", {})

        def extract_body(part):
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            for sub in part.get("parts", []):
                result = extract_body(sub)
                if result:
                    return result
            return ""

        body = extract_body(payload)
        if not body and payload.get("body", {}).get("data"):
            body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

        if body:
            emails.append({"subject": subject, "date": date, "body": body, "id": msg["id"]})

    return emails


def split_emails(emails):
    """Split alert emails into (listing_emails, departure_emails).

    Departure emails are Domain/REA sold / under-offer notifications. They must
    never reach the new-listing parser (it would re-create the sold property as
    a NEW entry); they are parsed by parse_emails_for_departures instead."""
    sys.path.insert(0, HERE)
    import parse_alert_email as parser
    listing_emails, departure_emails = [], []
    for em in emails:
        kind = parser.classify_email(em.get("subject", ""), em.get("body", ""))
        (departure_emails if kind == "departure" else listing_emails).append(em)
    return listing_emails, departure_emails


def parse_emails_for_departures(emails):
    """Extract departure records (sold / under offer) from departure emails.
    Returns a de-duplicated list of {url?, address?, suburb?, status, basis}."""
    sys.path.insert(0, HERE)
    import parse_alert_email as parser
    out, seen = [], set()
    for em in emails:
        for dep in parser.extract_departures(em.get("body", ""), em.get("subject", "")):
            k = dep.get("url") or f"{(dep.get('address') or '').lower()}|{(dep.get('suburb') or '').lower()}"
            if k in seen:
                continue
            seen.add(k)
            out.append(dep)
    return out


def _listing_id(url):
    """Numeric listing id at the end of a Domain/REA URL, or None. Sold-page
    URLs differ from the original listing URL (REA moves them under /sold/),
    but the numeric id is stable - it is the reliable join key."""
    import re
    m = re.search(r"(?:-|/)(\d{6,12})/?(?:\?.*)?$", url or "")
    return m.group(1) if m else None


def _norm_addr(address, suburb):
    return f"{(address or '').lower().strip()}|{(suburb or '').lower().strip()}"


def apply_departures(departures, listings, today=None):
    """Flag existing watchlist listings SOLD / UNDER_OFFER from departure records.

    Matching, in order: numeric listing id from the URL; exact URL; exact
    address+suburb; address-only (unique match required). Unmatched departures
    are IGNORED by design - a departure can flag a tracked listing but never
    inject a new one. A SOLD verdict upgrades UNDER_OFFER; a departure never
    downgrades SOLD back to UNDER_OFFER. Returns (applied_count, details)."""
    import datetime as _dt
    if today is None:
        today = _dt.date.today().isoformat()

    by_id, by_url, by_addr = {}, {}, {}
    for l in listings:
        lid = _listing_id(l.get("url"))
        if lid:
            by_id.setdefault(lid, l)
        if l.get("url"):
            by_url.setdefault(l["url"], l)
        if l.get("address"):
            by_addr.setdefault(_norm_addr(l.get("address"), l.get("suburb")), l)

    applied, details = 0, []
    for dep in departures:
        target = None
        dep_url = dep.get("url") or ""
        lid = _listing_id(dep_url)
        if lid and lid in by_id:
            target = by_id[lid]
        elif dep_url and dep_url in by_url:
            target = by_url[dep_url]
        elif dep.get("address"):
            target = by_addr.get(_norm_addr(dep.get("address"), dep.get("suburb")))
            if target is None and not dep.get("suburb"):
                # Address-only fallback: accept only an unambiguous match.
                addr = (dep["address"] or "").lower().strip()
                hits = [l for l in listings
                        if addr and addr == (l.get("address") or "").lower().strip()]
                if len(hits) == 1:
                    target = hits[0]
        if target is None:
            continue

        new_flag = "SOLD" if dep.get("status") == "sold" else "UNDER_OFFER"
        old_flag = target.get("change_flag")
        if old_flag == "SOLD":
            continue  # SOLD is terminal; never downgrade to UNDER_OFFER
        if old_flag == new_flag:
            continue
        target["change_flag"] = new_flag
        target["departed_on"] = target.get("departed_on") or today
        target["status_source"] = "email_alert"
        if dep.get("basis"):
            target["status_basis"] = dep["basis"]
        applied += 1
        details.append({
            "address": target.get("address"),
            "suburb": target.get("suburb"),
            "flag": new_flag,
        })
        print(f"  Departure: {target.get('address','?')}, {target.get('suburb','')} -> {new_flag}",
              file=sys.stderr)
    return applied, details


def parse_emails_for_listings(emails):
    """Parse email bodies to extract listings using parse_alert_email logic.
    Departure (sold / under-offer) emails are skipped here - route them through
    parse_emails_for_departures / apply_departures instead."""
    sys.path.insert(0, HERE)
    import parse_alert_email as parser
    import re

    emails, _departures = split_emails(emails)

    all_listings = {}
    for email in emails:
        # First try the standard URL-based parser
        listings = parser.extract(email["body"])
        for lst in listings:
            all_listings.setdefault(lst["url"], lst)

        # If no URLs found, try extracting by address (for tracking-redirect emails)
        if not listings:
            email_source = email.get("from", "domain")
            listings = extract_by_address(email["body"], email_source)
            for lst in listings:
                key = f"{lst.get('address', '')}|{lst.get('suburb', '')}"
                all_listings.setdefault(key, lst)

    result = list(all_listings.values())

    # Filter-provenance: if the REA saved search carries the accessibility
    # filters, tag REA-sourced listings as a provisional accessibility pass.
    if rea_filter_enabled():
        for lst in result:
            src = (lst.get("source") or "").lower()
            url = (lst.get("url") or "").lower()
            if "rea" in src or "realestate" in src or "realestate.com.au" in url:
                lst["accessibility_source"] = "rea_filter"

    return result


def generate_search_url(address: str, suburb: str, source: str = "domain") -> str:
    """Generate a search URL from address and suburb for Domain or REA."""
    import re

    if "realestate" in source.lower():
        # REA pattern: realestate.com.au/buy?searchTerm={address}+{suburb}+NSW
        full = f"{address} {suburb} NSW"
        full = re.sub(r'[/,\-]+', ' ', full)
        full = re.sub(r'[^a-zA-Z0-9\s]', '', full)
        full = re.sub(r'\s+', '+', full.strip())
        return f"https://www.realestate.com.au/buy?searchTerm={full}"
    else:
        # Domain pattern: domain.com.au/sale/?excludeunderoffer=1&street={address}+{suburb}
        import urllib.parse
        # Keep slashes and hyphens in address, URL-encode properly
        addr_clean = address.lower().strip()
        addr_encoded = urllib.parse.quote(addr_clean, safe='').replace('%20', '+')
        suburb_clean = suburb.lower().strip().replace(' ', '+')
        return f"https://www.domain.com.au/sale/?excludeunderoffer=1&street={addr_encoded}+{suburb_clean}"


def extract_by_address(body, email_source="domain"):
    """Extract listings by address when emails use tracking redirects instead of direct URLs."""
    import re
    import html

    # Target suburbs
    SUBURBS = r"(Zetland|Alexandria|Erskineville|Newtown|Camperdown|Glebe|Annandale|Leichhardt|Lilyfield|Rozelle|Balmain|Birchgrove|Marrickville|Dulwich Hill|Petersham|Stanmore|Enmore|Drummoyne)"

    # Address pattern: number/number Street Name, Suburb
    ADDRESS_RE = re.compile(
        rf"(\d+[A-Za-z]?(?:/\d+(?:-\d+)?)?)\s+([A-Za-z][A-Za-z\s]+?(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Place|Pl|Drive|Dr|Crescent|Cr|Parade|Pde|Way|Close|Cl|Court|Ct|Circuit|Boulevard|Blvd|Terrace|Tce))\s*,?\s*{SUBURBS}",
        re.I
    )

    # Price pattern
    PRICE_RE = re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s*[kKmM]|(?:\s*-\s*\$[\d,]+(?:\.\d+)?)?)?")

    # Strip tags but keep structure
    text = re.sub(r"<[^>]+>", " ", body)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)

    listings = []
    seen = set()

    for match in ADDRESS_RE.finditer(text):
        unit_street = match.group(1)
        street_name = match.group(2).strip()
        suburb = match.group(3).strip().title()

        address = f"{unit_street} {street_name}"

        # De-duplicate
        key = f"{address.lower()}|{suburb.lower()}"
        if key in seen:
            continue
        seen.add(key)

        # Look for price nearby (within 200 chars before)
        start = max(0, match.start() - 200)
        context = text[start:match.end() + 100]
        price_match = PRICE_RE.search(context)

        # Determine source based on email sender
        is_rea = "realestate" in email_source.lower()
        lst = {
            "address": address,
            "suburb": suburb,
            "url": generate_search_url(address, suburb, email_source),
            "source": "rea_search" if is_rea else "domain_search",
        }

        if price_match:
            price_text = price_match.group(0)
            # Parse numeric price and validate it's reasonable for Sydney property
            nums = re.findall(r"[\d,]+", price_text.replace(",", ""))
            if nums:
                try:
                    val = int(nums[0])
                    if "m" in price_text.lower():
                        val = int(val * 1_000_000)
                    elif "k" in price_text.lower():
                        val = int(val * 1_000)
                    elif val < 10000:  # Likely millions written as 1.5 etc
                        val = int(val * 1_000_000)
                    # Only accept prices in reasonable Sydney range ($800k - $10m)
                    if 800_000 <= val <= 10_000_000:
                        lst["price_guide_text"] = price_text
                        lst["price_min"] = val
                        lst["price_max"] = val
                except ValueError:
                    pass

        listings.append(lst)

    return listings


def merge_new_listings(new_listings, dry_run=False):
    """Geocode new listings and merge into listings.json."""
    import geocode as geocode_mod
    import score as score_mod

    # Load existing listings
    if os.path.exists(LISTINGS_PATH):
        with open(LISTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        existing_by_url = {l.get("url"): l for l in data.get("listings", []) if l.get("url")}
        existing_by_addr = {f"{l.get('address', '').lower()}|{l.get('suburb', '').lower()}": l
                           for l in data.get("listings", []) if l.get("address")}
    else:
        data = {"listings": [], "counts": {}}
        existing_by_url = {}
        existing_by_addr = {}

    # Find truly new listings (check both URL and address+suburb)
    truly_new = []
    for l in new_listings:
        url = l.get("url", "")
        addr_key = f"{l.get('address', '').lower()}|{l.get('suburb', '').lower()}"

        if url in existing_by_url:
            continue  # Already have this URL
        if addr_key in existing_by_addr:
            continue  # Already have this address
        truly_new.append(l)
    print(f"Found {len(truly_new)} new listings (of {len(new_listings)} parsed)", file=sys.stderr)

    if not truly_new:
        print("No new listings to add.", file=sys.stderr)
        return 0

    if dry_run:
        print("\nDry run - would add these listings:", file=sys.stderr)
        for l in truly_new:
            addr = l.get('address') or l.get('address_text') or l.get('url', '?')
            suburb = l.get('suburb', '')
            price = l.get('price_guide_text', '')
            print(f"  - {addr}, {suburb} {price}", file=sys.stderr)
        return len(truly_new)

    # Geocode new listings
    print("Geocoding new listings...", file=sys.stderr)
    geocode_mod.geocode_listings(truly_new)

    # Load amenities for scoring
    if os.path.exists(OSM_PATH):
        amenities = score_mod.load_amenities(OSM_PATH)
    else:
        amenities = {c: [] for c in score_mod.CATCHMENT_CLASSES}

    # Score new listings
    for l in truly_new:
        score_mod.score_listing(l, amenities)
        l["first_seen"] = dt.date.today().isoformat()
        l["last_seen"] = dt.date.today().isoformat()
        l["change_flag"] = "NEW"

    # Merge
    data["listings"].extend(truly_new)

    # Update counts (shared logic: active market counts + departed counts)
    import sweep as sweep_mod
    data["counts"] = sweep_mod.build_counts(data["listings"])

    # Write back
    with open(LISTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Added {len(truly_new)} new listings to {LISTINGS_PATH}", file=sys.stderr)
    return len(truly_new)


def main(argv):
    ap = argparse.ArgumentParser(description="Fetch property alerts from Gmail and ingest them.")
    ap.add_argument("--days", type=int, default=3, help="Look back N days (default: 3)")
    ap.add_argument("--dry-run", action="store_true", help="Preview without writing")
    ap.add_argument("--method", choices=["imap", "oauth", "auto"], default="auto",
                    help="Authentication method (default: auto-detect)")
    ap.add_argument("--reauth", action="store_true", help="Force re-authentication (OAuth only)")
    args = ap.parse_args(argv[1:])

    # Determine which method to use
    method = args.method
    if method == "auto":
        if os.path.exists(IMAP_CREDS_PATH):
            method = "imap"
        elif os.path.exists(OAUTH_CREDS_PATH) or os.path.exists(TOKEN_PATH):
            method = "oauth"
        else:
            print("No credentials found. Set up one of:", file=sys.stderr)
            print(f"\n  IMAP (easier): Create {IMAP_CREDS_PATH} with:", file=sys.stderr)
            print('    {"email": "you@gmail.com", "app_password": "xxxx xxxx xxxx xxxx"}', file=sys.stderr)
            print("\n  Get an App Password at: https://myaccount.google.com/apppasswords", file=sys.stderr)
            print(f"\n  OAuth (advanced): Place credentials.json at {OAUTH_CREDS_PATH}", file=sys.stderr)
            return 1

    # Fetch emails
    if method == "imap":
        emails = fetch_via_imap(days_back=args.days)
        if emails is None:
            return 1
    else:
        print("Connecting to Gmail via OAuth...", file=sys.stderr)
        service = get_gmail_service(force_reauth=args.reauth)
        emails = fetch_alert_emails(service, days_back=args.days)

    if not emails:
        print("No alert emails found.", file=sys.stderr)
        return 0

    listing_emails, departure_emails = split_emails(emails)
    print(f"{len(listing_emails)} new-listing emails, {len(departure_emails)} "
          f"sold/under-offer emails", file=sys.stderr)

    listings = parse_emails_for_listings(listing_emails)
    print(f"Parsed {len(listings)} unique listing URLs from emails", file=sys.stderr)

    if listings:
        merge_new_listings(listings, dry_run=args.dry_run)
    else:
        print("No new listings found in emails.", file=sys.stderr)

    # Sold / under-offer notifications -> flag matching watchlist entries.
    departures = parse_emails_for_departures(departure_emails)
    if departures and not args.dry_run and os.path.exists(LISTINGS_PATH):
        with open(LISTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        applied, details = apply_departures(departures, data.get("listings", []))
        if applied:
            import sweep as sweep_mod
            data["counts"] = sweep_mod.build_counts(data.get("listings", []))
            with open(LISTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"Flagged {applied} listing(s) sold/under offer.", file=sys.stderr)
    elif departures and args.dry_run:
        print(f"Dry run - {len(departures)} departure record(s) parsed:", file=sys.stderr)
        for d in departures:
            print(f"  - {d.get('address') or d.get('url','?')} -> {d['status']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
