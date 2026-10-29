# Okta Utility Scripts

A collection of standalone scripts for administering **Okta** (and the corporate Active Directory integration) at your organization. Each tool targets a specific operational task — provisioning users from AD, managing groups, tagging group attributes, creating shadow groups, and cleaning up orphaned groups.

> ⚠️ **Security note:** Every script authenticates to Okta with an API token (`SSWS` auth). Tokens are tied to your Okta identity — every action is audited under your account. **Never commit a token.** Always supply tokens via environment variables, and rotate any token that has been exposed. (See [Known issues](#known-issues) — one script currently contains a hard-coded token that should be removed.)

---

## Common prerequisites

- **Python 3.10+** for the Python scripts (they use `str | None` union syntax and the `requests` library).
- **Windows + PowerShell** on a corporate-managed workstation for the AD utility (it talks to Corp AD over LDAP).
- An **Okta API token** with admin permissions. To create one:
  1. Log in to the Okta Admin Console.
  2. Go to **Security → API → Tokens**.
  3. Click **Create Token** and follow the prompts.

Most scripts read configuration from environment variables. Note that variable names are **not** consistent across scripts (some use `OKTA_API_TOKEN`, others `OKTA_TOKEN`; some use `OKTA_ORG_URL`, others `OKTA_DOMAIN`) — check each tool's section below.

### Setting environment variables (PowerShell)

```powershell
$env:OKTA_ORG_URL  = "https://yourorg.okta.com"
$env:OKTA_API_TOKEN = "your_token_here"
```

### Python virtual environment

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt   # where a requirements.txt is provided; otherwise: pip install requests
```

---

## Repository contents

| Path | Type | Purpose |
| --- | --- | --- |
| [`find_okta_aws_sso_orphaned_groups.py`](find_okta_aws_sso_orphaned_groups.py) | Python | Find orphaned `ag-aws-` AWS SSO app groups and export them to CSV. |
| [`okta_rename_group_prefix.py`](okta_rename_group_prefix.py) | Python | Bulk-rename Okta groups by replacing a name prefix. |
| [`update_group-attributes.py`](update_group-attributes.py) | Python | Set a profile attribute (e.g. `privileged`) on matching Okta groups. |
| [`OktaPrivilegedGroupAttributes/`](OktaPrivilegedGroupAttributes/) | Python project | Same group-attribute updater, packaged with `requirements.txt` and historical CSV reports. |
| [`OktaShadowGroups/`](OktaShadowGroups/) | Python project | Create "shadow" (`SH-`) copies of AD-mastered groups and mirror their membership. |
| [`Okta_AD_Util/`](Okta_AD_Util/) | PowerShell project | Provision/update Okta users from corporate AD; export AD/Okta user lists. |

---

## `find_okta_aws_sso_orphaned_groups.py`

**What it does.** Identifies Okta groups of type `APP_GROUP` whose names start with `ag-aws-` (imported from AWS SSO / Identity Center) that are now **orphaned** — meaning their source application no longer exists in Okta (a `GET /api/v1/apps/{appId}` returns 404) or the group has no resolvable source-app link at all. Results are written to a CSV with the group ID, name, description, source app ID/label, timestamps, and a direct admin URL.

**Configuration (environment variables):**

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `OKTA_DOMAIN` | ✅ | — | e.g. `https://yourorg.okta.com` |
| `OKTA_API_TOKEN` | ✅ | — | Okta API token |
| `OKTA_OUTPUT_FILE` | | `okta_aws_sso_orphaned_groups.csv` | Output CSV path |
| `OKTA_GROUP_PREFIX` | | `ag-aws-` | Name prefix to search for |
| `OKTA_PAGE_LIMIT` | | `200` | Groups per page (max 200) |
| `OKTA_DRY_RUN` | | `0` | Set to `1` to print JSON to stdout instead of writing the CSV |

**Usage:**

```powershell
$env:OKTA_DOMAIN="https://yourorg.okta.com"
$env:OKTA_API_TOKEN="your_token_here"
python find_okta_aws_sso_orphaned_groups.py
```

This is **read-only** — it never modifies groups. It includes 429 rate-limit retry handling and caches app lookups to minimize API calls.

---

## `okta_rename_group_prefix.py`

**What it does.** Finds all Okta groups whose name starts with a given prefix and renames them, swapping the old prefix for a new one while preserving the rest of the name and all other profile fields. Runs in **dry-run mode by default** (preview only); requires `--execute` plus an interactive confirmation to actually apply changes.

**Arguments:**

| Argument | Required | Description |
| --- | --- | --- |
| `--domain` | ✅ | Okta domain, e.g. `mycompany.okta.com` (no `https://`) |
| `--old-prefix` | ✅ | Existing prefix to replace |
| `--new-prefix` | ✅ | New prefix to use |
| `--token` | | Okta API token (falls back to `OKTA_API_TOKEN` env var) |
| `--execute` | | Apply changes; without it the script previews only |
| `--rate-limit-delay` | | Seconds between API calls (default `0.05`) |

**Usage:**

```powershell
# Dry run (preview only)
python okta_rename_group_prefix.py --domain mycompany.okta.com --old-prefix "APP_" --new-prefix "SVC_"

# Actually rename (prompts for confirmation)
python okta_rename_group_prefix.py --domain mycompany.okta.com --old-prefix "APP_" --new-prefix "SVC_" --execute
```

---

## `update_group-attributes.py` and `OktaPrivilegedGroupAttributes/update_attributes.py`

These two files are **identical** Okta Group Profile Attribute Updaters. The `OktaPrivilegedGroupAttributes/` folder additionally ships a `requirements.txt` and dated `updated_groups_report_*.csv` audit files from previous runs.

**What it does.** Searches for native Okta groups (`type eq "OKTA_GROUP"`) whose name contains one or more search terms, then sets a boolean profile attribute (default `privileged = true`) on each match. It:

- Skips non-`OKTA_GROUP` types (app groups, built-in, etc.).
- Skips groups whose attribute is already set to the target value.
- De-duplicates groups seen across multiple search terms.
- Writes an audit CSV (`updated_groups_report_<YYYY-MM-DD>.csv`) recording previous/new values per group.
- Handles 429 rate limits and 5xx errors with retry/backoff.

Because Okta's group `PUT` replaces the **entire** profile, the script reads the existing profile and merges the new attribute in before sending.

**Configuration (environment variables):**

| Variable | Default |
| --- | --- |
| `OKTA_DOMAIN` | `https://yourorg.okta.com` |
| `OKTA_TOKEN` | *(hard-coded fallback — see [Known issues](#known-issues))* |

**Arguments:**

| Argument | Default | Description |
| --- | --- | --- |
| `--search-term TERM` | `ag-wiz-` | Name substring to match; repeatable for multiple terms |
| `--attribute` | `privileged` | Profile attribute name to set |
| `--value` | `true` | `true` or `false` |
| `--dry-run` | **enabled** | Simulate without applying (default) |
| `--no-dry-run` | | Apply changes for real |

**Usage:**

```powershell
$env:OKTA_DOMAIN="https://yourorg.okta.com"
$env:OKTA_TOKEN="your_token_here"

# Preview (default dry-run): mark groups matching ag-wiz- as privileged
python update_group-attributes.py

# Multiple search terms, applied for real
python update_group-attributes.py --search-term ag-wiz- --search-term ag-aws- --no-dry-run
```

---

## `OktaShadowGroups/`

A Python project that creates **shadow groups** in Okta from existing Active Directory–mastered groups. See [`OktaShadowGroups/Readme.md`](OktaShadowGroups/Readme.md) for full details.

**What it does.** Given a source prefix, it finds all Okta groups whose name **starts with** that prefix, and for each one that is **AD-mastered** (detected by the presence of a `samAccountName` profile attribute) it:

1. Creates a shadow group by replacing the source prefix with a new prefix (also passed on the command line) in the group name (skipping creation if it already exists).
2. Copies all members from the source group into the shadow group.
3. Finds any applications the source group is assigned to and assigns the shadow group to those same applications, copying the assignment's profile/priority where present.

Groups already starting with the new prefix are excluded so the script won't shadow its own output. It first validates the token and prints the operating identity for audit purposes. (A group-rule–based approach is stubbed out but disabled due to Okta API limitations.)

**Configuration (environment variables):**

| Variable | Description |
| --- | --- |
| `OKTA_ORG_URL` | Your Okta org URL, e.g. `https://yourorg.oktapreview.com` |
| `OKTA_API_TOKEN` | Okta API token |

**Usage:**

```powershell
cd OktaShadowGroups
python -m venv venv; .\venv\Scripts\activate
pip install -r requirements.txt

$env:OKTA_ORG_URL="https://yourorg.oktapreview.com"
$env:OKTA_API_TOKEN="your_token_here"

# Preview (default dry-run): show what would be created/added for groups starting with ag-aws-, replaced with SH-
python create-okta-shadow-groups.py ag-aws- SH-

# Apply for real
python create-okta-shadow-groups.py ag-aws- SH- --no-dry-run
```

Both `source_prefix` and `new_prefix` are **required positional arguments**. Dry-run is **enabled by default**; pass `--no-dry-run` to actually create shadow groups and add members.

---

## `Okta_AD_Util/`

A PowerShell tool for managing Okta users with data sourced from the corporate Active Directory. See [`Okta_AD_Util/Readme.md`](Okta_AD_Util/Readme.md) for full details.

**Requirements.** Must be run from a **corporate-managed Windows workstation** (it connects to Corp AD over LDAP using your logon server). Consists of the main script [`okta-ad-util.ps1`](Okta_AD_Util/okta-ad-util.ps1) and a supporting module [`OktaADSupport.psm1`](Okta_AD_Util/OktaADSupport.psm1) (helpers: `CreateOrUpdateOktaUser`, `Get-OktaUsers`, `GetADUser`, `Remove-Diacritics`, `DeleteUsersFromGroup`, `Log-Message`, etc.).

**Configuration (environment variables):** `OKTA_ORG_URL` and `OKTA_API_TOKEN`. If unset, the script prompts for them interactively.

**Modes of operation:**

| Mode | Trigger | What it does |
| --- | --- | --- |
| Single user | `--TID <TID/XID>` | Pulls the user (and manager) from AD and creates/updates them in Okta. `--FirstName`/`--LastName`/`--Email` override the AD values. |
| CSV batch | `--CSV <file>` | Reads users from a CSV and creates/updates each one from AD data. |
| Get AD IDs | `--GetIDs` | Enumerates all corporate TIDs/XIDs from Corp AD and writes them to a file. |
| Get Okta users | `--GetOktaUsers` | Exports current Okta users (filtered by `example.com` domain) to a timestamped CSV. |

**Optional switches:** `-Update` (allow updating existing users; otherwise they're skipped), `-DryRun` (don't actually write to Okta), `-D` (debug output), `-Activate` (activate users on creation), and `-DeleteUsersFromGroup -GroupId <id> -CSV <file>` (remove the CSV's users from a group).

**Usage:**

```powershell
cd Okta_AD_Util
$env:OKTA_ORG_URL="https://yourorg.oktapreview.com"
$env:OKTA_API_TOKEN="your_token_here"

.\okta-ad-util.ps1 --TID T123456 --FirstName John --LastName Doe --Email john.doe@example.com
.\okta-ad-util.ps1 --TID T123456
.\okta-ad-util.ps1 --CSV C:\TEMP\TID_XID_NotInAD.csv
.\okta-ad-util.ps1 --GetIDs
.\okta-ad-util.ps1 --GetOktaUsers
```

`oktaUsers-<timestamp>.csv` files in the folder are example exports from the `--GetOktaUsers` mode.

---

## Known issues

- **Hard-coded API token.** [`update_group-attributes.py`](update_group-attributes.py) and [`OktaPrivilegedGroupAttributes/update_attributes.py`](OktaPrivilegedGroupAttributes/update_attributes.py) contain a hard-coded Okta token as the `OKTA_TOKEN` fallback. This token should be **revoked/rotated** and removed from source; rely on the environment variable instead.
- **Committed virtual environments.** The `OktaShadowGroups/venv/` and `OktaPrivilegedGroupAttributes/venv/` directories contain full Python virtualenvs. These should be deleted and added to `.gitignore` (`venv/`, `__pycache__/`).
- **Inconsistent env var names.** Group scripts use `OKTA_TOKEN`/`OKTA_DOMAIN` while others use `OKTA_API_TOKEN`/`OKTA_ORG_URL`. Double-check the table in each section.

## Safety recommendations

- Run any write-capable script in **dry-run / preview mode first** and review the output.
- Prefer testing against an Okta **preview/sandbox** org (e.g. `*.oktapreview.com`) before production.
- Treat the generated `*.csv` reports as the audit trail of what each run changed.
