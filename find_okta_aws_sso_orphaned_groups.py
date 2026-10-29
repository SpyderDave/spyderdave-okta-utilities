#!/usr/bin/env python3
"""
find_okta_aws_sso_orphaned_groups.py
-------------------------------------
Identifies Okta groups whose names start with "ag-aws-" that were imported
from AWS SSO (Identity Center) and are now orphaned — i.e. their source
application no longer exists in Okta or the app-group link is broken.

What counts as a target group?
  - type == "APP_GROUP"  (imported from an external app, not a native Okta group)
  - Group name starts with "ag-aws-"

What counts as orphaned?
  - The group's source app no longer exists (GET /api/v1/apps/{appId} → 404), OR
  - The group has no resolvable source app link at all.

Output:
  okta_aws_sso_orphaned_groups.csv  (written to the same directory as this script)

Usage:
  export OKTA_DOMAIN="https://yourorg.okta.com"
  export OKTA_API_TOKEN="00your_token_here"
  python find_okta_aws_sso_orphaned_groups.py

Optional env vars:
  OKTA_OUTPUT_FILE   Override the output CSV path (default: okta_aws_sso_orphaned_groups.csv)
  OKTA_GROUP_PREFIX  Override the name prefix to search for (default: ag-aws-)
  OKTA_PAGE_LIMIT    Groups to fetch per page (default: 200, max: 200)
  OKTA_DRY_RUN       Set to "1" to print results to stdout instead of writing CSV
"""

import csv
import os
import re
import sys
import time
from urllib.parse import urlencode

try:
    import requests
except ImportError:
    sys.exit(
        "ERROR: 'requests' is not installed. Run:  pip install requests --break-system-packages"
    )

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OKTA_DOMAIN    = os.environ.get("OKTA_DOMAIN", "").rstrip("/")
OKTA_API_TOKEN = os.environ.get("OKTA_API_TOKEN", "")
OUTPUT_FILE    = os.environ.get("OKTA_OUTPUT_FILE", "okta_aws_sso_orphaned_groups.csv")
GROUP_PREFIX   = os.environ.get("OKTA_GROUP_PREFIX", "ag-aws-")
PAGE_LIMIT     = int(os.environ.get("OKTA_PAGE_LIMIT", "200"))
DRY_RUN        = os.environ.get("OKTA_DRY_RUN", "0") == "1"

# How long to wait between API calls to respect Okta rate limits (seconds)
RATE_LIMIT_SLEEP = 0.05   # 50 ms — conservative for most Okta rate-limit tiers


# ---------------------------------------------------------------------------
# Okta API helpers
# ---------------------------------------------------------------------------
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "Authorization": f"SSWS {OKTA_API_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    return s


def api_get(session: requests.Session, url: str) -> requests.Response:
    """GET with basic retry on 429 (rate limited)."""
    for attempt in range(5):
        resp = session.get(url)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("X-Rate-Limit-Reset", time.time() + 10))
            wait = max(retry_after - int(time.time()), 1)
            print(f"  [rate-limited] waiting {wait}s …", flush=True)
            time.sleep(wait)
            continue
        return resp
    resp.raise_for_status()
    return resp


def paginate(session: requests.Session, first_url: str) -> list[dict]:
    """Follow Okta's Link: <next> pagination and collect all results."""
    results = []
    url = first_url
    while url:
        resp = api_get(session, url)
        resp.raise_for_status()
        results.extend(resp.json())
        # Okta returns the next page URL in the Link header
        url = None
        link_header = resp.headers.get("Link", "")
        for part in link_header.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
        time.sleep(RATE_LIMIT_SLEEP)
    return results


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------
def get_all_app_groups(session: requests.Session) -> list[dict]:
    """
    Fetch APP_GROUP groups whose names start with GROUP_PREFIX.

    Uses the Okta `search` parameter so both the type and name prefix filters
    are applied server-side in a single request.  The older `q` + `type`
    combination is unreliable — Okta silently drops the `type` filter when
    `q` is also present.
    """
    search_expr = f'type eq "APP_GROUP" AND profile.name sw "{GROUP_PREFIX}"'
    params = urlencode({"search": search_expr, "limit": PAGE_LIMIT})
    url = f"{OKTA_DOMAIN}/api/v1/groups?{params}"
    print(f'Fetching APP_GROUP groups with name prefix "{GROUP_PREFIX}" …', flush=True)
    groups = paginate(session, url)
    # Belt-and-suspenders: verify client-side as well
    groups = [
        g for g in groups
        if g.get("type") == "APP_GROUP"
        and g.get("profile", {}).get("name", "").startswith(GROUP_PREFIX)
    ]
    print(f"  → {len(groups)} matching groups found.")
    return groups


def extract_source_app_id(group: dict) -> str | None:
    """
    Try to find the source app ID for an APP_GROUP.
    Okta embeds it in group['_links']['source']['href'] or group['_links']['apps']['href'].
    """
    links = group.get("_links", {})

    # Preferred: _links.source.href  →  .../api/v1/apps/{appId}
    source = links.get("source", {})
    href = source.get("href", "")
    if href:
        # Extract the app ID from the URL
        match = re.search(r"/apps/([^/?]+)", href)
        if match:
            return match.group(1)

    # Fallback: _links.apps.href  →  .../api/v1/groups/{groupId}/apps
    #   This is a list endpoint; we'd need an extra call, skip for now.
    return None


def app_exists(session: requests.Session, app_id: str, cache: dict) -> bool:
    """Return True if the Okta app still exists (cached to avoid duplicate calls)."""
    if app_id in cache:
        return cache[app_id]
    url = f"{OKTA_DOMAIN}/api/v1/apps/{app_id}"
    resp = api_get(session, url)
    exists = resp.status_code == 200
    cache[app_id] = exists
    time.sleep(RATE_LIMIT_SLEEP)
    return exists



def get_app_label(session: requests.Session, app_id: str, label_cache: dict) -> str | None:
    """Fetch and cache the app label / name from Okta."""
    if app_id in label_cache:
        return label_cache[app_id]
    url = f"{OKTA_DOMAIN}/api/v1/apps/{app_id}"
    resp = api_get(session, url)
    if resp.status_code == 200:
        label = resp.json().get("label") or resp.json().get("name")
        label_cache[app_id] = label
        return label
    label_cache[app_id] = None
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # Validate configuration
    errors = []
    if not OKTA_DOMAIN:
        errors.append("OKTA_DOMAIN environment variable is not set.")
    if not OKTA_API_TOKEN:
        errors.append("OKTA_API_TOKEN environment variable is not set.")
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        sys.exit(1)

    session    = make_session()
    app_exists_cache: dict[str, bool]     = {}
    app_label_cache:  dict[str, str|None] = {}
    orphaned:         list[dict]          = []

    all_app_groups = get_all_app_groups(session)

    print("Analysing groups for AWS SSO origin and orphan status …", flush=True)

    for i, group in enumerate(all_app_groups, 1):
        group_id   = group.get("id", "")
        profile    = group.get("profile", {})
        name       = profile.get("name", "")
        description = profile.get("description", "")
        created    = group.get("created", "")
        last_updated = group.get("lastUpdated", "")

        source_app_id = extract_source_app_id(group)

        # Resolve the source app label (best-effort; app may already be gone)
        source_app_label: str | None = None
        is_orphaned = False

        if source_app_id:
            source_app_label = get_app_label(session, source_app_id, app_label_cache)
            is_orphaned = not app_exists(session, source_app_id, app_exists_cache)
        else:
            # No source link at all — definitively orphaned
            is_orphaned = True

        # Only keep groups that look like they came from AWS SSO
        # Progress indicator every 50 groups
        if i % 50 == 0:
            print(f"  … processed {i}/{len(all_app_groups)} groups", flush=True)

        if is_orphaned:
            orphaned.append(
                {
                    "group_id":         group_id,
                    "group_name":       name,
                    "description":      description,
                    "source_app_id":    source_app_id or "(none)",
                    "source_app_label": source_app_label or "(not found / deleted)",
                    "created":          created,
                    "last_updated":     last_updated,
                    "okta_url":         f"{OKTA_DOMAIN}/admin/group/{group_id}",
                }
            )

    print(f"\n{'='*60}")
    print(f"Found {len(orphaned)} orphaned AWS SSO group(s).")
    print(f"{'='*60}\n")

    if not orphaned:
        print("No orphaned AWS SSO groups found. Nothing to export.")
        return

    fieldnames = [
        "group_id",
        "group_name",
        "description",
        "source_app_id",
        "source_app_label",
        "created",
        "last_updated",
        "okta_url",
    ]

    if DRY_RUN:
        # Print to stdout
        import json
        print(json.dumps(orphaned, indent=2))
        return

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(orphaned)

    print(f"Report saved → {OUTPUT_FILE}")
    print("\nSample rows:")
    for row in orphaned[:5]:
        print(f"  {row['group_name']}  |  app: {row['source_app_label']}  |  {row['okta_url']}")
    if len(orphaned) > 5:
        print(f"  … and {len(orphaned) - 5} more (see CSV).")


if __name__ == "__main__":
    main()
