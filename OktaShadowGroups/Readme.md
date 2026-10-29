# PURPOSE

This script creates shadow groups in Okta based on existing Active Directory-mastered groups. It takes a source prefix and a new prefix as command line arguments and identifies all Okta groups whose name **starts with** the source prefix. For each matching AD-mastered group, a shadow group is created by replacing the source prefix with the new prefix, its membership is mirrored, and it is assigned to any applications the source group is assigned to.

For example, to create shadow groups for all groups starting with `ag-aws-`, replacing that prefix with `SH-`:
```
python create-okta-shadow-groups.py ag-aws- SH-
```
`ag-aws-shared-services` becomes `SH-shared-services`.

Key Features:
- Only processes Active Directory-mastered groups (checks for samAccountName attribute)
- Matches groups whose name **starts with** the given source prefix
- Creates shadow groups by replacing the source prefix with the given new prefix
- Automatically copies all members from source group to shadow group
- Implements rate limiting to respect Okta API limits
- Skips creation if shadow group already exists
- Excludes groups that already start with the new prefix, so the script won't shadow its own output
- Finds applications assigned to the source group and assigns the shadow group to those same applications, copying the assignment's profile/priority where present
- Supports a dry-run mode (enabled by default) that previews changes without writing to Okta

# Requirements

To use this script, you must set two environment variables:

- OKTA_ORG_URL: Your Okta organization URL
- OKTA_API_TOKEN: Your Okta API token

Example:

**Powershell**
```powershell
$env:OKTA_ORG_URL="https://yourorg.oktapreview.com"
$env:OKTA_API_TOKEN="my_super_scary_token"
```

# Get Yourself an Okta Token

Note: you must have admin permissions in Okta to create an API Token.

1. Login to the Okta Admin Console
2. Select Security--->API
3. Select the Tokens Tab
4. Click "Create Token" and follow the prompts

NOTE: Your token should be protected! Do not share it. Any action performed with the token will be traced to your Okta Identity!

# Running the Script

1. Create and activate a Python Virtual Environment
```bash
python -m venv venv
.\venv\Scripts\activate
```

2. Install requirements
```bash
pip install -r requirements.txt
```

3. Run the script

Dry-run mode is **on by default** — it prints what would be created/added without making any changes:
```bash
python create-okta-shadow-groups.py <source_prefix> <new_prefix>
```

To apply the changes for real, pass `--no-dry-run`:
```bash
python create-okta-shadow-groups.py <source_prefix> <new_prefix> --no-dry-run
```

# How It Works

1. The script first validates your API token and retrieves your Okta identity for audit purposes
2. It retrieves all Okta groups using pagination to handle large sets of groups
3. Filters groups whose name starts with the source prefix, excluding groups that already start with the new prefix
4. For each matching group:
   - Verifies it is AD-mastered by checking for samAccountName attribute
   - Checks if shadow group already exists (skips if it does)
   - Creates new shadow group, replacing the source prefix with the new prefix in the name
   - Retrieves all members from source group
   - Adds each member to the shadow group
   - Retrieves any applications assigned to the source group
   - For each assigned application, fetches the source group's assignment (profile/priority) and assigns the shadow group to that application with the same assignment

# Error Handling

- Validates required environment variables are set
- Checks API responses for errors
- Implements pagination for large group lists
- Rate limiting (50ms delay between API calls)
- Skips non-AD groups
- Checks for existing shadow groups before creation
- Skips application assignment entirely if the source group has no assigned applications

# Output

The script provides detailed progress output:
- Confirms OKTA endpoint and running identity
- Shows each group being processed
- Indicates AD group verification
- Shows member addition progress
- Shows application assignment progress
- Reports success/failure for each operation

# Notes

- Shadow groups are created with a description indicating the source group and creator
- The script uses the Okta API v1
- API calls are rate-limited to avoid hitting Okta's rate limits
- Group rules are currently not supported due to API limitations
