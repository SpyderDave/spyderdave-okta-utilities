#!/usr/bin/env python3
"""
Delete Okta groups matching a given prefix.

Required environment variables:
  OKTA_URL    - Your Okta org URL (e.g. https://yourorg.okta.com)
  OKTA_TOKEN  - An API token with group management permissions

Usage:
  python delete_okta_groups.py --prefix "test-" [--execute]

Flags:
  --prefix PREFIX   Group name prefix to match (required)
  --execute         Actually delete groups (default: dry run)
  --limit N         Max groups to delete in one run (default: unlimited)
"""

import argparse
import os
import sys
import time
import requests


def get_env() -> tuple[str, str]:
    okta_url = os.environ.get("OKTA_ORG_URL", "").rstrip("/")
    okta_token = os.environ.get("OKTA_API_TOKEN", "")

    if not okta_url:
        sys.exit("Error: OKTA_URL environment variable is not set.")
    if not okta_token:
        sys.exit("Error: OKTA_TOKEN environment variable is not set.")

    return okta_url, okta_token


def fetch_groups(okta_url: str, headers: dict, prefix: str) -> list[dict]:
    """Fetch all groups whose name starts with prefix, handling pagination."""
    groups = []
    url = f"{okta_url}/api/v1/groups"
    params = {
        "q": prefix,      # Okta's q param filters by name prefix
        "limit": 200,
    }

    while url:
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        page = resp.json()

        # Filter client-side to ensure exact prefix match (Okta's q is fuzzy)
        for group in page:
            name = group.get("profile", {}).get("name", "")
            if name.startswith(prefix):
                groups.append(group)

        # Follow pagination via Link header
        link = resp.headers.get("Link", "")
        url = None
        params = {}
        for part in link.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                break

    return groups


def delete_group(okta_url: str, headers: dict, group: dict) -> None:
    group_id = group["id"]
    name = group.get("profile", {}).get("name", group_id)
    url = f"{okta_url}/api/v1/groups/{group_id}"
    resp = requests.delete(url, headers=headers)
    resp.raise_for_status()
    print(f"  [DELETED] {name} ({group_id})")


def main():
    parser = argparse.ArgumentParser(description="Delete Okta groups by name prefix.")
    parser.add_argument("--prefix", required=True, help="Group name prefix to match")
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Perform actual deletion (omit for dry run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of groups to delete (optional safety cap)",
    )
    args = parser.parse_args()

    okta_url, okta_token = get_env()
    headers = {
        "Authorization": f"SSWS {okta_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"Mode: {mode}")
    print(f"Okta URL: {okta_url}")
    print(f"Prefix: {args.prefix!r}")
    print()

    print("Fetching matching groups...")
    groups = fetch_groups(okta_url, headers, args.prefix)

    if not groups:
        print("No groups found matching the prefix. Nothing to do.")
        return

    print(f"Found {len(groups)} group(s) matching prefix {args.prefix!r}:\n")
    for g in groups:
        name = g.get("profile", {}).get("name", g["id"])
        print(f"  - {name} ({g['id']})")

    if args.limit is not None and len(groups) > args.limit:
        print(f"\nLimit of {args.limit} reached — truncating to first {args.limit} group(s).")
        groups = groups[: args.limit]

    print()

    if not args.execute:
        print(f"DRY RUN complete — {len(groups)} group(s) would be deleted.")
        print("Re-run with --execute to perform actual deletion.")
        return

    confirm = input(f"About to delete {len(groups)} group(s). Type 'yes' to confirm: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        return

    print()
    errors = []
    for group in groups:
        try:
            delete_group(okta_url, headers, group)
            time.sleep(0.1)  # gentle rate limiting
        except requests.HTTPError as e:
            name = group.get("profile", {}).get("name", group["id"])
            msg = f"  [FAILED]  {name} ({group['id']}): {e.response.status_code} {e.response.text}"
            print(msg)
            errors.append(msg)

    print()
    print(f"Done. {len(groups) - len(errors)} deleted, {len(errors)} failed.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()