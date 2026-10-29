#!/usr/bin/env python3
"""
Look up each user's last Okta login from a CSV of email addresses, and
optionally flag anyone who logged in on or after a termination/offboarding
date also supplied in the CSV.

For every email address in the input CSV, this queries the Okta System Log
for the most recent matching login event (default event type:
"user.session.start"). Because System Log retention is limited (commonly
90 days, depending on your Okta plan), it falls back to the user's profile
`lastLogin` field (from the Users API) when no matching log event is found,
so older logins aren't reported as "never logged in" just because the log
has aged out.

If --termination-column is supplied, each user's last login is compared
against their termination date and flagged in the report if the login
happened after that date (a login on the termination date itself is not
flagged). A second CSV containing only the flagged users is also written.

Required environment variables:
  OKTA_ORG_URL    Your Okta org URL, e.g. "https://acme.okta.com"
  OKTA_API_TOKEN  An Okta API token with read access to logs and users

Usage:
  python okta_last_login_by_email.py --input emails.csv
  python okta_last_login_by_email.py --input emails.csv --output report.csv
  python okta_last_login_by_email.py --input emails.csv --event-types "user.session.start,user.authentication.sso"
  python okta_last_login_by_email.py --input emails.csv --since 2026-01-01 --no-fallback

  # Email in column D, termination date in column C (spreadsheet-style column letters)
  python okta_last_login_by_email.py --input TCSUsers.csv --email-column D --termination-column C

PowerShell:
  $env:OKTA_ORG_URL = "https://acme.okta.com"
  $env:OKTA_API_TOKEN = "00your_token_here"
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

EMAIL_RE = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+")

# Formats tried, in order, when parsing a termination date cell.
TERMINATION_DATE_FORMATS = [
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%B %d, %Y",
    "%b %d, %Y",
]

MAX_RETRIES = 5
RETRY_BACKOFF = 2


def get_env() -> tuple[str, str]:
    okta_url = os.environ.get("OKTA_ORG_URL", "").rstrip("/")
    okta_token = os.environ.get("OKTA_API_TOKEN", "")

    if not okta_url or not okta_token:
        sys.exit(
            "ERROR: OKTA_ORG_URL and OKTA_API_TOKEN environment variables must be set.\n"
            '  PowerShell: $env:OKTA_ORG_URL="https://acme.okta.com"; '
            '$env:OKTA_API_TOKEN="00your_token_here"'
        )

    return okta_url, okta_token


def okta_get(url: str, headers: dict, params: dict | None = None) -> requests.Response:
    """GET with retry/backoff on 429 and 5xx responses."""
    for attempt in range(MAX_RETRIES):
        resp = requests.get(url, headers=headers, params=params)

        if resp.status_code == 429:
            wait = int(resp.headers.get("x-rate-limit-reset") or resp.headers.get("Retry-After") or RETRY_BACKOFF)
            print(f"    Rate limited, waiting {wait}s...")
            time.sleep(wait + 1)
            continue

        if resp.status_code >= 500:
            wait = RETRY_BACKOFF ** attempt
            print(f"    Server error {resp.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue

        return resp

    raise RuntimeError(f"Max retries exceeded for GET {url}")


def column_letter_to_index(letters: str) -> int:
    """Convert a spreadsheet-style column letter ("A", "D", "AA", ...) to a 0-based index."""
    index = 0
    for ch in letters.upper():
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def resolve_column(rows: list[list[str]], spec: str) -> int:
    """Resolve a --*-column argument to a 0-based column index.

    Accepts either a spreadsheet-style column letter (e.g. "D") or a header
    name to look up (matched case-insensitively against the first few rows,
    to tolerate a title row above the real header row).
    """
    if re.fullmatch(r"[A-Za-z]{1,2}", spec):
        return column_letter_to_index(spec)

    for row in rows[:10]:
        for i, cell in enumerate(row):
            if cell.strip().lower() == spec.strip().lower():
                return i

    raise ValueError(f'Could not find a column named "{spec}" in the first 10 rows of the input file')


def parse_termination_date(value: str):
    """Parse a termination date cell into a date object, or None if unparseable/blank."""
    value = (value or "").strip()
    if not value:
        return None

    for fmt in TERMINATION_DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    return None


def read_user_records(input_path: str, email_spec: str | None, termination_spec: str | None) -> list[dict]:
    """Extract a de-duplicated, ordered list of user records from a CSV file.

    Each record is {"email": str, "termination_date_raw": str, "termination_date": date | None}.
    Works whether the CSV has a header row naming the email column, a title
    row above the header, or is just a bare list of addresses. Rows whose
    resolved email cell isn't actually an email (title/header rows, blanks)
    are skipped automatically.
    """
    with open(input_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    if not rows:
        return []

    email_col = resolve_column(rows, email_spec) if email_spec else None
    termination_col = resolve_column(rows, termination_spec) if termination_spec else None

    records: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        if email_col is not None:
            candidates = [row[email_col]] if email_col < len(row) else []
        else:
            candidates = row  # auto-detect: scan every cell for an email-shaped value

        email = None
        for cell in candidates:
            match = EMAIL_RE.search(cell or "")
            if match:
                email = match.group(0).strip()
                break

        if not email:
            continue

        key = email.lower()
        if key in seen:
            continue
        seen.add(key)

        termination_raw = ""
        if termination_col is not None and termination_col < len(row):
            termination_raw = row[termination_col].strip()

        records.append(
            {
                "email": email,
                "termination_date_raw": termination_raw,
                "termination_date": parse_termination_date(termination_raw),
            }
        )

    return records


def parse_okta_timestamp(ts: str):
    """Parse an Okta ISO-8601 UTC timestamp (e.g. 2026-05-28T13:03:04.000Z) into a datetime."""
    ts = ts.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def build_log_filter(email: str, event_types: list[str]) -> str:
    email_escaped = email.replace('"', '\\"')
    actor_clause = f'actor.alternateId eq "{email_escaped}"'

    if len(event_types) == 1:
        event_clause = f'eventType eq "{event_types[0]}"'
    else:
        event_clause = " or ".join(f'eventType eq "{et}"' for et in event_types)
        event_clause = f"({event_clause})"

    return f"{actor_clause} and {event_clause}"


def get_last_login_from_logs(okta_url: str, headers: dict, email: str, event_types: list[str], since: str | None):
    """Return (timestamp_str, event_type) for the most recent matching log event, or (None, None)."""
    url = f"{okta_url}/api/v1/logs"
    params = {
        "filter": build_log_filter(email, event_types),
        "sortOrder": "DESCENDING",
        "limit": 1,
    }
    if since:
        params["since"] = since

    resp = okta_get(url, headers, params)
    resp.raise_for_status()
    events = resp.json()

    if not events:
        return None, None

    event = events[0]
    return event.get("published"), event.get("eventType")


def get_user_profile(okta_url: str, headers: dict, email: str):
    """Return the Okta user object for an email, or None if not found."""
    url = f"{okta_url}/api/v1/users/{email}"
    resp = okta_get(url, headers, params=None)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Look up each user's last Okta login from a CSV of emails.")
    parser.add_argument("--input", required=True, help="Path to input CSV file containing email addresses")
    parser.add_argument("--output", help="Path to output CSV report (default: okta_last_login_report_<date>.csv)")
    parser.add_argument(
        "--flagged-output",
        help="Path to a CSV containing only flagged (post-termination login) users "
        "(default: okta_flagged_post_termination_logins_<date>.csv). Only written when "
        "--termination-column is set.",
    )
    parser.add_argument(
        "--email-column",
        help='Column containing email addresses: a header name (e.g. "Email") or a '
        'spreadsheet-style column letter (e.g. "D"). Default: auto-detect.',
    )
    parser.add_argument(
        "--termination-column",
        help='Column containing each user\'s termination/offboarding date: a header name '
        'or a spreadsheet-style column letter (e.g. "C"). When given, users who logged in '
        "after this date are flagged in the report and written to a flagged-only CSV.",
    )
    parser.add_argument(
        "--event-types",
        default="user.session.start",
        help='Comma-separated Okta System Log eventType(s) to treat as a login (default: "user.session.start")',
    )
    parser.add_argument(
        "--since",
        help="Only search System Log events on/after this ISO 8601 date (e.g. 2026-01-01), speeds up the search",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Do not fall back to the user profile's lastLogin field when no log event is found",
    )
    args = parser.parse_args()

    okta_url, okta_token = get_env()
    headers = {
        "Authorization": f"SSWS {okta_token}",
        "Accept": "application/json",
    }

    event_types = [e.strip() for e in args.event_types.split(",") if e.strip()]

    try:
        records = read_user_records(args.input, args.email_column, args.termination_column)
    except ValueError as e:
        sys.exit(f"ERROR: {e}")

    if not records:
        sys.exit(f"No email addresses found in {args.input}")

    output_path = args.output or f"okta_last_login_report_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv"
    check_termination = args.termination_column is not None
    flagged_output_path = args.flagged_output or (
        f"okta_flagged_post_termination_logins_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv"
        if check_termination
        else None
    )

    print(f"Okta org:     {okta_url}")
    print(f"Input file:   {args.input}")
    print(f"Users found:  {len(records)}")
    print(f"Event types:  {', '.join(event_types)}")
    print(f"Fallback to profile lastLogin: {not args.no_fallback}")
    if check_termination:
        print(f"Flagging logins after termination date (column: {args.termination_column})")
    print()

    results = []
    flagged_count = 0

    for i, record in enumerate(records, start=1):
        email = record["email"]
        print(f"[{i}/{len(records)}] {email}")

        row = {
            "email": email,
            "okta_status": "",
            "last_login_utc": "",
            "source": "",
            "event_type": "",
            "termination_date": record["termination_date_raw"],
            "logged_in_after_termination": "",
            "notes": "",
        }

        if check_termination and record["termination_date_raw"] and record["termination_date"] is None:
            row["notes"] = f'Could not parse termination date "{record["termination_date_raw"]}"'

        try:
            timestamp, event_type = get_last_login_from_logs(okta_url, headers, email, event_types, args.since)
            last_login_dt = None

            if timestamp:
                row["last_login_utc"] = timestamp
                row["source"] = "system_log"
                row["event_type"] = event_type
                last_login_dt = parse_okta_timestamp(timestamp)
                print(f"    Last login (log): {timestamp} ({event_type})")
            elif not args.no_fallback:
                user = get_user_profile(okta_url, headers, email)
                if user is None:
                    row["notes"] = "User not found in Okta"
                    print("    User not found in Okta")
                else:
                    row["okta_status"] = user.get("status", "")
                    last_login = user.get("lastLogin")
                    if last_login:
                        row["last_login_utc"] = last_login
                        row["source"] = "profile_lastLogin"
                        row["notes"] = "No matching log event within retention window; used profile lastLogin"
                        last_login_dt = parse_okta_timestamp(last_login)
                        print(f"    Last login (profile fallback): {last_login}")
                    else:
                        row["notes"] = "No login on record"
                        print("    No login on record")
            else:
                row["notes"] = "No matching log event found"
                print("    No matching log event found")

            if check_termination and last_login_dt and record["termination_date"]:
                # Strictly after the termination date, i.e. on/after the start of the next day —
                # a login on the termination date itself is not flagged.
                day_after_termination = datetime(
                    record["termination_date"].year,
                    record["termination_date"].month,
                    record["termination_date"].day,
                    tzinfo=timezone.utc,
                ) + timedelta(days=1)
                if last_login_dt >= day_after_termination:
                    row["logged_in_after_termination"] = "YES"
                    flagged_count += 1
                    print(f"    *** FLAGGED: logged in after termination date {record['termination_date']} ***")

        except requests.HTTPError as e:
            row["notes"] = f"Error: {e.response.status_code} {e.response.text[:200]}"
            print(f"    ERROR: {e.response.status_code} {e.response.text[:200]}")
        except Exception as e:
            row["notes"] = f"Error: {e}"
            print(f"    ERROR: {e}")

        results.append(row)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "email",
            "okta_status",
            "last_login_utc",
            "source",
            "event_type",
            "termination_date",
            "logged_in_after_termination",
            "notes",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nReport written to {output_path}")

    if check_termination:
        flagged_rows = [r for r in results if r["logged_in_after_termination"] == "YES"]
        with open(flagged_output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(flagged_rows)

        print(f"Flagged {flagged_count} user(s) who logged in after their termination date.")
        print(f"Flagged-only log written to {flagged_output_path}")


if __name__ == "__main__":
    main()
