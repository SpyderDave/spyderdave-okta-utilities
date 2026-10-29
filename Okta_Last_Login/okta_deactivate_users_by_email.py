#!/usr/bin/env python3
"""
Deactivate Okta users listed by email address in a CSV.

Takes the same input CSV format as okta_last_login_by_email.py: the email
column can be given as a header name (e.g. "Email") or a spreadsheet-style
column letter (e.g. "D"), or left unset to auto-detect. Title/header rows
that don't contain a real email address are skipped automatically. This is
particularly intended to be fed the "flagged" output of
okta_last_login_by_email.py (users who logged in after their termination
date), but works with any CSV of email addresses.

For each email, the user is looked up in Okta and deactivated via
POST /api/v1/users/{id}/lifecycle/deactivate, which sets their status to
DEPROVISIONED (they can be re-activated later via the Okta admin console
or the Users API if needed).

Runs in DRY RUN mode by default — nothing is changed until you pass
--execute, and even then you must type "yes" at a confirmation prompt
before anything is deactivated.

Required environment variables:
  OKTA_ORG_URL    Your Okta org URL, e.g. "https://acme.okta.com"
  OKTA_API_TOKEN  An Okta API token with user management permissions

Usage:
  # Preview only (default)
  python okta_deactivate_users_by_email.py --input flagged.csv

  # Email addresses in column D
  python okta_deactivate_users_by_email.py --input TCSUsers.csv --email-column D

  # Actually deactivate (prompts for confirmation)
  python okta_deactivate_users_by_email.py --input flagged.csv --execute

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
from datetime import datetime, timezone

import requests

EMAIL_RE = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+")

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


def okta_request(method: str, url: str, headers: dict, **kwargs) -> requests.Response:
    """Request with retry/backoff on 429 and 5xx responses."""
    for attempt in range(MAX_RETRIES):
        resp = requests.request(method, url, headers=headers, **kwargs)

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

    raise RuntimeError(f"Max retries exceeded for {method} {url}")


def column_letter_to_index(letters: str) -> int:
    """Convert a spreadsheet-style column letter ("A", "D", "AA", ...) to a 0-based index."""
    index = 0
    for ch in letters.upper():
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def resolve_column(rows: list[list[str]], spec: str) -> int:
    """Resolve --email-column to a 0-based column index: a column letter or a header name."""
    if re.fullmatch(r"[A-Za-z]{1,2}", spec):
        return column_letter_to_index(spec)

    for row in rows[:10]:
        for i, cell in enumerate(row):
            if cell.strip().lower() == spec.strip().lower():
                return i

    raise ValueError(f'Could not find a column named "{spec}" in the first 10 rows of the input file')


def read_emails(input_path: str, email_spec: str | None) -> list[str]:
    """Extract a de-duplicated, ordered list of email addresses from a CSV file.

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

    emails: list[str] = []
    seen: set[str] = set()

    for row in rows:
        candidates = [row[email_col]] if email_col is not None and email_col < len(row) else row

        for cell in candidates:
            match = EMAIL_RE.search(cell or "")
            if match:
                email = match.group(0).strip()
                key = email.lower()
                if key not in seen:
                    seen.add(key)
                    emails.append(email)
                break

    return emails


def get_user(okta_url: str, headers: dict, email: str):
    """Return the Okta user object for an email, or None if not found."""
    url = f"{okta_url}/api/v1/users/{email}"
    resp = okta_request("GET", url, headers)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def deactivate_user(okta_url: str, headers: dict, user_id: str) -> None:
    url = f"{okta_url}/api/v1/users/{user_id}/lifecycle/deactivate"
    resp = okta_request("POST", url, headers)
    resp.raise_for_status()


def main():
    parser = argparse.ArgumentParser(description="Deactivate Okta users listed by email address in a CSV.")
    parser.add_argument("--input", required=True, help="Path to input CSV file containing email addresses")
    parser.add_argument(
        "--email-column",
        help='Column containing email addresses: a header name (e.g. "Email") or a '
        'spreadsheet-style column letter (e.g. "D"). Default: auto-detect.',
    )
    parser.add_argument(
        "--output",
        help="Path to output CSV audit report (default: okta_deactivation_report_<date>.csv)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Actually deactivate users (omit for dry run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of users to deactivate in one run (optional safety cap)",
    )
    args = parser.parse_args()

    okta_url, okta_token = get_env()
    headers = {
        "Authorization": f"SSWS {okta_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        emails = read_emails(args.input, args.email_column)
    except ValueError as e:
        sys.exit(f"ERROR: {e}")

    if not emails:
        sys.exit(f"No email addresses found in {args.input}")

    if args.limit is not None and len(emails) > args.limit:
        print(f"Limit of {args.limit} reached — truncating to first {args.limit} user(s).")
        emails = emails[: args.limit]

    output_path = args.output or f"okta_deactivation_report_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv"
    mode = "EXECUTE" if args.execute else "DRY RUN"

    print(f"Mode:         {mode}")
    print(f"Okta org:     {okta_url}")
    print(f"Input file:   {args.input}")
    print(f"Users found:  {len(emails)}")
    print()

    if args.execute:
        print("The following users will be DEACTIVATED in Okta:")
        for email in emails:
            print(f"  - {email}")
        print()
        confirm = input(f"About to deactivate {len(emails)} user(s). Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted. No changes made.")
            return
        print()

    results = []
    deactivated = 0
    skipped = 0
    failed = 0

    for i, email in enumerate(emails, start=1):
        print(f"[{i}/{len(emails)}] {email}")

        row = {
            "email": email,
            "okta_user_id": "",
            "previous_status": "",
            "action": "",
            "result": "",
            "notes": "",
        }

        try:
            user = get_user(okta_url, headers, email)

            if user is None:
                row["result"] = "skipped"
                row["notes"] = "User not found in Okta"
                skipped += 1
                print("    SKIP: user not found in Okta")
                results.append(row)
                continue

            user_id = user["id"]
            status = user.get("status", "")
            row["okta_user_id"] = user_id
            row["previous_status"] = status

            if status == "DEPROVISIONED":
                row["action"] = "none"
                row["result"] = "skipped"
                row["notes"] = "Already deactivated"
                skipped += 1
                print(f"    SKIP: already deactivated (status={status})")
                results.append(row)
                continue

            if not args.execute:
                row["action"] = "would deactivate"
                row["result"] = "dry-run"
                print(f"    DRY RUN: would deactivate (current status={status})")
                results.append(row)
                continue

            deactivate_user(okta_url, headers, user_id)
            row["action"] = "deactivated"
            row["result"] = "success"
            deactivated += 1
            print(f"    DEACTIVATED (was {status})")

        except requests.HTTPError as e:
            row["result"] = "failed"
            row["notes"] = f"Error: {e.response.status_code} {e.response.text[:200]}"
            failed += 1
            print(f"    ERROR: {e.response.status_code} {e.response.text[:200]}")
        except Exception as e:
            row["result"] = "failed"
            row["notes"] = f"Error: {e}"
            failed += 1
            print(f"    ERROR: {e}")

        results.append(row)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["email", "okta_user_id", "previous_status", "action", "result", "notes"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print()
    print("=" * 60)
    print("Run Summary")
    print("=" * 60)
    print(f"  Mode                : {mode}")
    print(f"  Users processed     : {len(emails)}")
    print(f"  Deactivated         : {deactivated}")
    print(f"  Skipped             : {skipped}")
    print(f"  Failed              : {failed}")
    print("=" * 60)
    print(f"\nReport written to {output_path}")

    if not args.execute:
        print("\nThis was a DRY RUN — no users were deactivated. Re-run with --execute to apply changes.")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
