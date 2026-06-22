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
IMAP_CREDS_PATH = os.path.join(DATA, ".gmail_credentials.json")
OAUTH_CREDS_PATH = os.path.join(DATA, ".gmail_oauth.json")
TOKEN_PATH = os.path.join(DATA, ".gmail_token.json")
LISTINGS_PATH = os.path.join(DATA, "listings.json")
OSM_PATH = os.path.join(DATA, "osm_amenities.geojson")

# Gmail API scopes - read-only access to emails
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Alert senders to search for
ALERT_SENDERS = ["noreply@domain.com.au", "noreply@realestate.com.au",
                 "alerts@domain.com.au", "alerts@realestate.com.au"]


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


def parse_emails_for_listings(emails):
    """Parse email bodies to extract listings using parse_alert_email logic."""
    sys.path.insert(0, HERE)
    import parse_alert_email as parser

    all_listings = {}
    for email in emails:
        listings = parser.extract(email["body"])
        for lst in listings:
            # De-duplicate by URL
            all_listings.setdefault(lst["url"], lst)

    return list(all_listings.values())


def merge_new_listings(new_listings, dry_run=False):
    """Geocode new listings and merge into listings.json."""
    import geocode as geocode_mod
    import score as score_mod

    # Load existing listings
    if os.path.exists(LISTINGS_PATH):
        with open(LISTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        existing = {l.get("url"): l for l in data.get("listings", [])}
    else:
        data = {"listings": [], "counts": {}}
        existing = {}

    # Find truly new listings
    truly_new = [l for l in new_listings if l.get("url") not in existing]
    print(f"Found {len(truly_new)} new listings (of {len(new_listings)} parsed)", file=sys.stderr)

    if not truly_new:
        print("No new listings to add.", file=sys.stderr)
        return 0

    if dry_run:
        print("\nDry run - would add these listings:", file=sys.stderr)
        for l in truly_new:
            print(f"  - {l.get('address_text', l.get('url'))}", file=sys.stderr)
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

    # Update counts
    active = [l for l in data["listings"] if l.get("change_flag") not in ("WITHDRAWN", "SOLD")]
    data["counts"] = {
        "total": len(active),
        "tier1_pass": sum(1 for l in active if l.get("tier1", {}).get("pass")),
        "new": sum(1 for l in active if l.get("change_flag") == "NEW"),
        "price_changed": sum(1 for l in active if l.get("change_flag") == "PRICE_CHANGED"),
        "sold": sum(1 for l in active if l.get("change_flag") == "SOLD"),
        "withdrawn": sum(1 for l in active if l.get("change_flag") == "WITHDRAWN"),
    }

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

    listings = parse_emails_for_listings(emails)
    print(f"Parsed {len(listings)} unique listing URLs from emails", file=sys.stderr)

    if not listings:
        print("No listings found in emails.", file=sys.stderr)
        return 0

    added = merge_new_listings(listings, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
