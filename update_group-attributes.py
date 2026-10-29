import argparse
import argparse
import requests
import time
import logging
import os
import sys
import csv
from datetime import datetime, timezone

# ---------------------------------------------------
# Configuration
# ---------------------------------------------------

OKTA_DOMAIN = os.getenv("OKTA_DOMAIN", "https://yourorg.okta.com")
OKTA_TOKEN = os.getenv("OKTA_TOKEN", "your_token_here")

SEARCH_TERMS = ["ag-wiz-"]  # default; overridden by --search-term CLI argument

MAX_RETRIES = 5
RETRY_BACKOFF = 2

timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
CSV_FILE = f"updated_groups_report_{timestamp}.csv"

updated_groups = []

# ---------------------------------------------------
# Logging
# ---------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# ---------------------------------------------------
# Headers
# ---------------------------------------------------

HEADERS = {
    "Authorization": f"SSWS {OKTA_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# ---------------------------------------------------
# HTTP helper with retry + rate limit handling
# ---------------------------------------------------

def okta_request(method, url, **kwargs):

    for attempt in range(MAX_RETRIES):

        response = requests.request(method, url, headers=HEADERS, **kwargs)

        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", RETRY_BACKOFF))
            logging.warning(f"Rate limited. Sleeping {wait}s")
            time.sleep(wait)
            continue

        if response.status_code >= 500:
            sleep = RETRY_BACKOFF ** attempt
            logging.warning(f"Server error {response.status_code}. Retry in {sleep}s")
            time.sleep(sleep)
            continue

        if response.status_code >= 400:
            logging.error(f"Error {response.status_code}: {response.text}")
            response.raise_for_status()

        return response

    raise Exception("Max retries exceeded")

# ---------------------------------------------------
# Fetch groups using search
# ---------------------------------------------------

def get_groups(search_term):

    url = f"{OKTA_DOMAIN}/api/v1/groups"
    params = {
        "search": f'profile.name co "{search_term}" and type eq "OKTA_GROUP"',
        "limit": 200
    }

    page = 0

    while url:

        page += 1
        logging.info(f"[{search_term}] Fetching page {page} of groups from Okta...")

        r = okta_request("GET", url, params=params)
        groups = r.json()

        logging.info(f"[{search_term}] Page {page}: received {len(groups)} group(s)")

        for g in groups:
            yield g

        url = None
        params = None

        if "link" in r.headers:

            links = r.headers["link"].split(",")

            for link in links:

                if 'rel="next"' in link:
                    url = link.split(";")[0].strip()[1:-1]
                    logging.info(f"[{search_term}] Next page URL found, continuing pagination...")

# ---------------------------------------------------
# Update group attribute
# ---------------------------------------------------

def update_group(group, attribute_name, attribute_value, dry_run):

    group_id = group["id"]
    name = group["profile"].get("name")
    group_type = group["type"]

    if group_type != "OKTA_GROUP":
        logging.info(f"Skipping non-OKTA group: {name}")
        return

    previous_value = group["profile"].get(attribute_name)

    # Build full profile payload — Okta PUT replaces the entire profile,
    # so we must include all existing fields plus our new attribute.
    existing_profile = dict(group["profile"])
    existing_profile[attribute_name] = attribute_value

    payload = {
        "profile": existing_profile
    }

    logging.info(f"Updating group: {name}")

    if not dry_run:

        url = f"{OKTA_DOMAIN}/api/v1/groups/{group_id}"
        okta_request("PUT", url, json=payload)

    updated_groups.append({
        "group_id": group_id,
        "group_name": name,
        "group_type": group_type,
        "previous_value": previous_value,
        "attribute_name": attribute_name,
        "new_value": attribute_value,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

    if dry_run:
        logging.info("DRY RUN - change not applied")
    else:
        logging.info(f"Updated {name}")

# ---------------------------------------------------
# Write CSV report
# ---------------------------------------------------

def write_report():

    if not updated_groups:
        logging.info("No updates made. No report generated.")
        return

    with open(CSV_FILE, "w", newline="") as csvfile:

        fieldnames = [
            "group_id",
            "group_name",
            "group_type",
            "attribute_name",
            "previous_value",
            "new_value",
            "timestamp"
        ]

        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for row in updated_groups:
            writer.writerow(row)

    logging.info(f"Report written to {CSV_FILE}")

# ---------------------------------------------------
# Main
# ---------------------------------------------------

def main():

    parser = argparse.ArgumentParser(description="Okta Group Profile Attribute Updater")
    parser.add_argument(
        "--search-term",
        action="append",
        dest="search_terms",
        metavar="TERM",
        help="Group name prefix/substring to search for (can be specified multiple times; default: ag-wiz-)"
    )
    parser.add_argument(
        "--attribute",
        default="privileged",
        help="Name of the group profile attribute to update (default: privileged)"
    )
    parser.add_argument(
        "--value",
        choices=["true", "false"],
        default="true",
        help="Boolean value to set the attribute to: true or false (default: true)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Simulate changes without applying them (default: enabled)"
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Apply changes for real"
    )
    args = parser.parse_args()

    dry_run = args.dry_run
    attribute_name = args.attribute
    attribute_value = args.value.lower() == "true"
    search_terms = args.search_terms if args.search_terms else SEARCH_TERMS

    if not OKTA_DOMAIN or not OKTA_TOKEN:
        logging.error("OKTA_DOMAIN and OKTA_API_TOKEN must be set")
        sys.exit(1)

    logging.info("=" * 60)
    logging.info("Okta Group Profile Attribute Updater")
    logging.info("=" * 60)
    logging.info(f"Okta Domain  : {OKTA_DOMAIN}")
    logging.info(f"Search Terms : {', '.join(search_terms)}")
    logging.info(f"Attribute    : {attribute_name}")
    logging.info(f"Value        : {attribute_value}")
    logging.info(f"Dry Run      : {dry_run}")
    logging.info("=" * 60)

    total = 0
    skipped_non_okta = 0
    skipped_already_set = 0
    updated = 0
    failed = 0
    evaluated = 0
    seen_ids = set()

    for search_term in search_terms:

        logging.info(f"--- Searching for groups containing: '{search_term}' ---")

        for group in get_groups(search_term):

            group_id = group["id"]

            if group_id in seen_ids:
                logging.info(f"Skipping duplicate group (already processed): {group['profile'].get('name', '')}")
                continue

            seen_ids.add(group_id)
            name = group["profile"].get("name", "")
            evaluated += 1

            if not any(term.lower() in name.lower() for term in search_terms):
                logging.info(f"[{evaluated}] SKIP (no match) : {name}")
                continue

            group_type = group["type"]
            total += 1
            logging.info(f"[{evaluated}] MATCH           : {name} (type: {group_type})")

            # Only process native Okta groups — skip APP_GROUP, BUILT_IN, etc.
            if group_type != "OKTA_GROUP":
                skipped_non_okta += 1
                logging.info(f"  -> Skipping: not an OKTA_GROUP (type={group_type})")
                continue

            # Skip if attribute is already set to the desired value
            if group["profile"].get(attribute_name) is attribute_value:
                skipped_already_set += 1
                logging.info(f"  -> Skipping: {attribute_name} is already {attribute_value}")
                continue

            try:
                update_group(group, attribute_name, attribute_value, dry_run)
                updated += 1

            except Exception as e:
                failed += 1
                logging.error(f"Failed processing {name}: {e}")

    logging.info("=" * 60)
    logging.info("Run Summary")
    logging.info("=" * 60)
    logging.info(f"  Groups evaluated        : {total}")
    logging.info(f"  Updated (or queued)     : {updated}")
    logging.info(f"  Skipped (already set)   : {skipped_already_set}")
    logging.info(f"  Skipped (non-OKTA type) : {skipped_non_okta}")
    logging.info(f"  Failed                  : {failed}")
    logging.info(f"  Dry Run                 : {dry_run}")
    logging.info("=" * 60)

    write_report()

if __name__ == "__main__":
    main()
