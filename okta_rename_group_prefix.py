#!/usr/bin/env python3
"""
okta_rename_group_prefix.py

Replace a name prefix on Okta groups.

Usage:
    python okta_rename_group_prefix.py --domain <okta-domain> --old-prefix <OLD> --new-prefix <NEW> [--execute]

Environment variable:
    OKTA_API_TOKEN   Your Okta API token (or pass via --token)

Examples:
    # Dry run (preview only):
    python okta_rename_group_prefix.py --domain mycompany.okta.com --old-prefix "APP_" --new-prefix "SVC_"

    # Actually rename:
    python okta_rename_group_prefix.py --domain mycompany.okta.com --old-prefix "APP_" --new-prefix "SVC_" --execute
"""

import argparse
import os
import sys
import time
import requests


def build_headers(token: str) -> dict:
    return {
        "Authorization": f"SSWS {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def fetch_groups_with_prefix(base_url: str, headers: dict, prefix: str) -> list[dict]:
    """Fetch all Okta groups whose name starts with `prefix`, handling pagination."""
    groups = []
    # Okta supports a 'q' search for groups by name prefix
    url = f"{base_url}/api/v1/groups"
    params = {"q": prefix, "limit": 200}

    while url:
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        page = resp.json()

        # Filter strictly: the 'q' param is a starts-with search but confirm locally
        for group in page:
            if group["profile"]["name"].startswith(prefix):
                groups.append(group)

        # Follow the 'next' link header if present (pagination)
        url = None
        params = None  # params are already encoded in the next URL
        link_header = resp.headers.get("Link", "")
        for part in link_header.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                break

    return groups


def rename_group(base_url: str, headers: dict, group: dict, new_name: str) -> dict:
    """Update a group's name via the Okta API."""
    group_id = group["id"]
    url = f"{base_url}/api/v1/groups/{group_id}"

    # Preserve all existing profile fields; only swap the name
    updated_profile = dict(group["profile"])
    updated_profile["name"] = new_name

    resp = requests.put(url, headers=headers, json={"profile": updated_profile})
    resp.raise_for_status()
    return resp.json()


def confirm_proceed(count: int) -> bool:
    print(f"\nReady to rename {count} group(s).")
    answer = input("Proceed? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def main():
    parser = argparse.ArgumentParser(
        description="Replace a name prefix on Okta groups.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--domain",
        required=True,
        help="Your Okta domain, e.g. mycompany.okta.com",
    )
    parser.add_argument(
        "--old-prefix",
        required=True,
        dest="old_prefix",
        help="The existing group name prefix to replace.",
    )
    parser.add_argument(
        "--new-prefix",
        required=True,
        dest="new_prefix",
        help="The new prefix to use instead.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("OKTA_API_TOKEN"),
        help="Okta API token. Falls back to OKTA_API_TOKEN env var.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform the renames. Without this flag the script runs in dry-run mode.",
    )
    parser.add_argument(
        "--rate-limit-delay",
        type=float,
        default=0.05,
        dest="rate_limit_delay",
        help="Seconds to wait between API calls (default: 0.05). Increase if you hit 429s.",
    )

    args = parser.parse_args()

    if not args.token:
        print(
            "ERROR: No API token provided. Set OKTA_API_TOKEN or pass --token.",
            file=sys.stderr,
        )
        sys.exit(1)

    base_url = f"https://{args.domain.rstrip('/')}"
    headers = build_headers(args.token)

    print(f"Searching for groups with prefix '{args.old_prefix}' …")
    try:
        groups = fetch_groups_with_prefix(base_url, headers, args.old_prefix)
    except requests.HTTPError as exc:
        print(f"ERROR fetching groups: {exc}", file=sys.stderr)
        sys.exit(1)

    if not groups:
        print("No groups found with that prefix. Nothing to do.")
        sys.exit(0)

    # Build rename plan
    plan = []
    for group in groups:
        old_name = group["profile"]["name"]
        new_name = args.new_prefix + old_name[len(args.old_prefix):]
        plan.append((group, old_name, new_name))

    # Preview
    col_w = max(len(old) for _, old, _ in plan) + 2
    print(f"\n{'OLD NAME':<{col_w}}  NEW NAME")
    print("-" * (col_w + 2 + col_w))
    for _, old_name, new_name in plan:
        print(f"{old_name:<{col_w}}  {new_name}")

    if not args.execute:
        print(
            f"\nDry run: {len(plan)} group(s) would be renamed."
            "\nRe-run with --execute to apply changes."
        )
        sys.exit(0)

    # Confirm before writing
    if not confirm_proceed(len(plan)):
        print("Aborted.")
        sys.exit(0)

    # Apply renames
    success, failed = 0, 0
    for group, old_name, new_name in plan:
        try:
            rename_group(base_url, headers, group, new_name)
            print(f"  ✓  {old_name}  →  {new_name}")
            success += 1
        except requests.HTTPError as exc:
            print(f"  ✗  {old_name}  — FAILED: {exc}", file=sys.stderr)
            failed += 1
        time.sleep(args.rate_limit_delay)

    print(f"\nDone. {success} renamed, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
