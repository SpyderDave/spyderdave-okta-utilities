import argparse
import requests
import csv
import sys
from time import sleep
import json
import os

API_TOKEN = os.environ.get('OKTA_API_TOKEN')
OKTA_ORG_URL = os.environ.get('OKTA_ORG_URL')

GROUP_PATTERN = 'ag-aws-'

# API Headers
headers = {
    'Authorization': f'SSWS {API_TOKEN}',
    'Content-Type': 'application/json',
}

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def print_section(title, width=80):
    print('=' * width)
    print(title)
    print('=' * width)

def print_kv(label, value, width=16):
    print(f"{label:<{width}}: {value}")

def print_group_header(index, total, group_name, width=80):
    print()
    print('-' * width)
    print(f"[{index}/{total}] {group_name}")
    print('-' * width)

# Function to get all Okta groups
def get_okta_groups():
    url = f'{OKTA_ORG_URL}/api/v1/groups'
    groups = []

    while url:
        sleep(0.05)
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            groups.extend(response.json())
            # Pagination: check for the next page of results
            url = response.links.get('next', {}).get('url', None)
        else:
            print(f"  [ERROR] Fetching groups: {response.status_code} - {response.text}")
            break
    return groups

# Function to get group members for a specific group
def get_group_members(group_id):
    url = f'{OKTA_ORG_URL}/api/v1/groups/{group_id}/users'
    members = []

    while url:
        sleep(0.05)
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            members.extend(response.json())
            # Pagination: check for the next page of results
            url = response.links.get('next', {}).get('url', None)
        else:
            print(f"  [ERROR] Fetching members for group {group_id}: {response.status_code} - {response.text}")
            break
    return members

# Function to get all applications assigned to a specific group
def get_group_apps(group_id):
    url = f'{OKTA_ORG_URL}/api/v1/groups/{group_id}/apps'
    apps = []

    while url:
        sleep(0.05)
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            apps.extend(response.json())
            # Pagination: check for the next page of results
            url = response.links.get('next', {}).get('url', None)
        else:
            print(f"  [ERROR] Fetching apps for group {group_id}: {response.status_code} - {response.text}")
            break
    return apps

# Function to fetch the profile/priority of an existing app-group assignment
def get_app_group_assignment(app_id, group_id):
    url = f'{OKTA_ORG_URL}/api/v1/apps/{app_id}/groups/{group_id}'
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"    [ERROR] Fetching app-group assignment for app {app_id}, group {group_id}: {response.status_code} - {response.text}")
        return {}

# Function to assign the shadow group to an application, mirroring the source group's assignment.
# Returns (success: bool, error: str|None)
def assign_shadow_group_to_app(app_id, shadow_group_id, source_assignment):
    payload = {}
    profile = source_assignment.get('profile')
    if profile:
        payload['profile'] = profile
    priority = source_assignment.get('priority')
    if priority is not None:
        payload['priority'] = priority

    url = f'{OKTA_ORG_URL}/api/v1/apps/{app_id}/groups/{shadow_group_id}'
    response = requests.put(url, headers=headers, json=payload)
    sleep(0.05)
    if response.status_code == 200:
        return True, None
    return False, f"{response.status_code} - {response.text}"

# Function to filter groups by source prefix, excluding groups already using the new prefix
def filter_groups(groups, source_prefix, new_prefix):
    return [
        group for group in groups
        if group['profile']['name'].lower().startswith(source_prefix.lower())
        and not group['profile']['name'].lower().startswith(new_prefix.lower())
    ]

# Function to build the shadow group name by replacing the source prefix with the new prefix
def build_shadow_group_name(group_name, source_prefix, new_prefix):
    if group_name.lower().startswith(source_prefix.lower()):
        return new_prefix + group_name[len(source_prefix):]
    return new_prefix + group_name

# Function to find or create the shadow group in Okta.
# Returns (shadow_group_id: str|None, status: "exists"|"dry_run"|"created"|"error: <detail>")
def get_or_create_shadow_group(shadow_group_name, source_group_name, actor, dry_run):
    existing_id = check_group_exists(OKTA_ORG_URL, API_TOKEN, shadow_group_name)
    if existing_id:
        return existing_id, "exists"

    if dry_run:
        return None, "dry_run"

    url = f'{OKTA_ORG_URL}/api/v1/groups'
    payload = {
        'profile': {
            'name': shadow_group_name,
            'description': f'Shadow group for {source_group_name} Created by: {actor}'
        }
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        return response.json()['id'], "created"
    return None, f"error: {response.status_code} - {response.text}"

def create_group_rule(SOURCE_GROUP_ID,TARGET_GROUP_ID):
    # Construct the API URL for creating the group rule
    url = f'{OKTA_ORG_URL}/api/v1/groups/rules'
    # Define the rule payload to add members of the source group to the target group
    rule_payload = {
        "type": "group_rule",
        "name": "Add Source Group Members to Target Group",
        "conditions": {
            "people": {
                "groups": {
                    "id": SOURCE_GROUP_ID
                }
            }
        },
        "actions": {
            "assignUserToGroup": {
                "group": {
                    "id": TARGET_GROUP_ID
                }
            }
        }
    }

    # Send the request to create the rule
    response = requests.post(url, headers=headers, data=json.dumps(rule_payload))

    # Check the response and handle accordingly
    if response.status_code == 201:
        print("Group rule created successfully!")
        print("Rule details:", response.json())
    else:
        print(f"Failed to create group rule. Status code: {response.status_code}")
        print("Response:", response.text)


def write_to_csv(filtered_groups):
    with open('okta_groups_and_members.csv', mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['Group Name', 'Member Name(s)'])

        for group in filtered_groups:
            group_name = group['profile']['name']
            group_members = get_group_members(group['id'])
            #member_names = [member['profile']['login'] for member in group_members]
            #writer.writerow([group_name, ', '.join(member_names)])
            count = 1
            for member in group_members:
                if (count == 1 ):
                    count = count + 1
                    writer.writerow([group_name, member['profile']['login']])
                else:
                    writer.writerow(['', member['profile']['login']])

def get_okta_api_token_info(okta_domain, api_token):
    """
    Function to retrieve general Okta API information using an API token.

    Args:
        okta_domain (str): The Okta domain, e.g., 'https://your-okta-domain.okta.com'
        api_token (str): The Okta API token to authenticate requests.

    Returns:
        dict: Information about the Okta session or response from Okta API
    """
    headers = {
        'Authorization': f"SSWS {api_token}",
        'Accept': 'application/json',
    }

    # Make a simple API call to get information, like system status
    url = f"{okta_domain}/api/v1/users/me"

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()  # Returns user info associated with the API token
    else:
        return {"error": "Failed to retrieve user information", "status_code": response.status_code}

def check_group_exists(okta_url,api_token,group_name):
    # Not found is the default
    Found = 0

    groups = get_okta_groups()
    for group in groups:
        if (group['profile']['name'] == group_name):
            Found = group['id']
            break
    return Found

def check_if_AD_group(group_name):
    groups = get_okta_groups()
    for group in groups:
        if 'profile' in group and 'samAccountName' in group['profile']:
            if group['profile']['samAccountName'] == group_name:
                return True
    return False

# Main execution flow
def main():

    parser = argparse.ArgumentParser(description="Create Okta shadow groups for AD-mastered groups")
    parser.add_argument(
        "source_prefix",
        help="Group name prefix to match (e.g. ag-aws-). Only AD-mastered groups whose name starts with this prefix are processed."
    )
    parser.add_argument(
        "new_prefix",
        help="Prefix to replace the source prefix with in the shadow group name (e.g. SH-)"
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

    source_prefix = args.source_prefix
    new_prefix = args.new_prefix
    dry_run = args.dry_run

    if not API_TOKEN:
        print("******************************************************************************************")
        print("***ERROR*** OKTA_API_TOKEN environment variable is not set.")
        print("******************************************************************************************")
        sys.exit(1)
    if not OKTA_ORG_URL:
        print("******************************************************************************************")
        print("***ERROR*** OKTA_ORG_URL environment variable is not set.")
        print("******************************************************************************************")
        sys.exit(1)

    # Get the identity we are operating as
    token_info = get_okta_api_token_info(OKTA_ORG_URL, API_TOKEN)
    myActor = token_info['profile']['displayName']

    print_section("OKTA SHADOW GROUP SYNC")
    print_kv("Okta Endpoint", OKTA_ORG_URL)
    print_kv("Running As", myActor)
    print_kv("Source Prefix", source_prefix)
    print_kv("New Prefix", new_prefix)
    print_kv("Dry Run", dry_run)
    print('=' * 80)

    # Step 1: Get all Okta groups
    print("\nFetching Okta groups...")
    groups = get_okta_groups()

    # Step 2: Filter groups by source prefix
    filtered_groups = filter_groups(groups, source_prefix, new_prefix)
    total = len(filtered_groups)
    print(f"Found {total} group(s) matching prefix '{source_prefix}'")

    stats = {
        "skipped_non_ad": 0,
        "shadow_created": 0,
        "shadow_existing": 0,
        "shadow_failed": 0,
        "members_added": 0,
        "member_failures": 0,
        "apps_assigned": 0,
        "app_failures": 0,
    }

    for index, group in enumerate(filtered_groups, start=1):
        group_name = group['profile']['name']
        print_group_header(index, total, group_name)

        is_ad_group = check_if_AD_group(group_name)
        print_kv("AD-mastered", "yes" if is_ad_group else "no")
        if not is_ad_group:
            print("  Skipping: not an Active Directory group")
            stats["skipped_non_ad"] += 1
            continue

        shadow_group_name = build_shadow_group_name(group_name, source_prefix, new_prefix)
        shadowGroupId, status = get_or_create_shadow_group(shadow_group_name, group_name, myActor, dry_run)

        if status == "exists":
            print_kv("Shadow group", f"{shadow_group_name} (already exists)")
            stats["shadow_existing"] += 1
        elif status == "dry_run":
            print_kv("Shadow group", f"{shadow_group_name} [DRY RUN] would be created")
            stats["shadow_created"] += 1
        elif status == "created":
            print_kv("Shadow group", f"{shadow_group_name} (created)")
            stats["shadow_created"] += 1
        else:
            print_kv("Shadow group", f"{shadow_group_name} FAILED - {status}")
            stats["shadow_failed"] += 1
            continue

        # Members
        group_members = get_group_members(group['id'])
        print(f"  Members ({len(group_members)}):")
        for member in group_members:
            login = member['profile']['login']
            if dry_run:
                print(f"    [DRY RUN] would add {login}")
                stats["members_added"] += 1
                continue
            url = f'{OKTA_ORG_URL}/api/v1/groups/{shadowGroupId}/users/{member["id"]}'
            response = requests.put(url, headers=headers)
            sleep(0.05)
            if response.status_code == 204:
                print(f"    added {login}")
                stats["members_added"] += 1
            else:
                print(f"    FAILED to add {login} ({response.status_code})")
                stats["member_failures"] += 1

        # Application assignments
        source_apps = get_group_apps(group['id'])
        if not source_apps:
            print("  Application assignments: none")
            continue

        print(f"  Application assignments ({len(source_apps)}):")
        for app in source_apps:
            app_id = app['id']
            app_label = app.get('label', app_id)
            if dry_run:
                print(f"    [DRY RUN] would assign to {app_label}")
                stats["apps_assigned"] += 1
                continue
            assignment = get_app_group_assignment(app_id, group['id'])
            success, error = assign_shadow_group_to_app(app_id, shadowGroupId, assignment)
            if success:
                print(f"    assigned to {app_label}")
                stats["apps_assigned"] += 1
            else:
                print(f"    FAILED to assign to {app_label} ({error})")
                stats["app_failures"] += 1

    print()
    print_section("SUMMARY")
    print_kv("Groups matched", total, width=26)
    print_kv("Skipped (non-AD)", stats["skipped_non_ad"], width=26)
    print_kv("Shadow groups created", stats["shadow_created"], width=26)
    print_kv("Shadow groups existing", stats["shadow_existing"], width=26)
    print_kv("Shadow group failures", stats["shadow_failed"], width=26)
    print_kv("Members added", stats["members_added"], width=26)
    print_kv("Member failures", stats["member_failures"], width=26)
    print_kv("App assignments", stats["apps_assigned"], width=26)
    print_kv("App assignment failures", stats["app_failures"], width=26)
    if dry_run:
        print("\nDRY RUN - no changes were actually made. Re-run with --no-dry-run to apply.")
    print('=' * 80)

if __name__ == "__main__":
    main()
