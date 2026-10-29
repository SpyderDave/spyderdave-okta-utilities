# Okta Last Login & Post-Termination Deactivation

This folder has two related scripts that both read a CSV of email addresses (with columns given as either a header name or a spreadsheet-style column letter, auto-detecting title/header rows):

| Script | Purpose |
| --- | --- |
| [`okta_last_login_by_email.py`](okta_last_login_by_email.py) | Looks up each user's last Okta login, and optionally flags anyone who logged in after their termination date |
| [`okta_deactivate_users_by_email.py`](okta_deactivate_users_by_email.py) | Deactivates the Okta accounts for a list of emails — intended to be fed the flagged-output CSV from the script above |

A typical workflow chains the two:
```bash
# 1. Find who logged in after their termination date
python okta_last_login_by_email.py --input TCSUsers.csv --email-column D --termination-column C

# 2. Review okta_flagged_post_termination_logins_<date>.csv, then deactivate those accounts
python okta_deactivate_users_by_email.py --input okta_flagged_post_termination_logins_<date>.csv --execute
```

---

# Requirements

Both scripts need two environment variables:

- OKTA_ORG_URL: Your Okta organization URL
- OKTA_API_TOKEN: Your Okta API token

Example:

**Powershell**
```powershell
$env:OKTA_ORG_URL="https://yourorg.okta.com"
$env:OKTA_API_TOKEN="my_super_scary_token"
```

Install requirements:
```bash
pip install requests
```

# Get Yourself an Okta Token

Note: you must have admin permissions in Okta to create an API Token.

1. Login to the Okta Admin Console
2. Select Security--->API
3. Select the Tokens Tab
4. Click "Create Token" and follow the prompts

NOTE: Your token should be protected! Do not share it. Any action performed with the token will be traced to your Okta Identity!

`okta_last_login_by_email.py` needs read access to the System Log and Users APIs. `okta_deactivate_users_by_email.py` additionally needs user management permissions (a standard admin token covers both).

---

# `okta_last_login_by_email.py`

## Purpose

This script looks up each user's last Okta login from a CSV of email addresses. For every address, it queries the Okta System Log for the most recent matching login event (default event type: `user.session.start`). Because System Log retention is limited on most Okta plans (commonly ~90 days), it falls back to the user's profile `lastLogin` field (from the Users API) when no matching log event is found, so older logins aren't misreported as "never logged in" just because the log has aged out. Results are written to a CSV report.

It can also flag **post-termination logins**: if the input CSV also has a termination/offboarding date column, pass `--termination-column` and any user whose last login falls strictly *after* that date (a login on the termination date itself doesn't count) is flagged in the main report **and** written to a separate flagged-only CSV — useful for auditing whether offboarded accounts were actually deprovisioned in time, and as direct input to `okta_deactivate_users_by_email.py`.

For example:
```bash
python okta_last_login_by_email.py --input emails.csv

# Email addresses in column D, termination dates in column C
python okta_last_login_by_email.py --input TCSUsers.csv --email-column D --termination-column C
```

Key Features:
- Reads email addresses from a CSV — auto-detects an `email`-style header column, or scans every cell for anything email-shaped if there's no header
- Column arguments accept either a header name (e.g. `Email`) or a spreadsheet-style column letter (e.g. `D`), and automatically skip title/header rows that don't contain a real email
- Queries the Okta System Log for the most recent login event per user, sorted descending so only one API call is needed per user
- Supports multiple login event types via `--event-types` (comma-separated), e.g. to also count SSO logins
- Optional `--since` bound to narrow/speed up the log search
- Falls back to the user's profile `lastLogin` attribute when no log event is found within the retention window (disable with `--no-fallback`)
- Optionally flags any user whose last login is strictly after a termination date supplied in the same CSV (`--termination-column`)
- Flags users that don't exist in Okta
- Implements retry/backoff on 429 rate limits and 5xx errors
- Writes a CSV report with per-user status, last login timestamp, source of that timestamp, and termination-flag results
- Also writes a second, flagged-only CSV containing just the users who logged in after their termination date, when `--termination-column` is used
- Read-only — it never modifies users or groups

## Running the Script

Prepare an input CSV of email addresses. It can have a header row with an `email` column, or just be a bare list of addresses:
```csv
email
alice@example.com
bob@example.com
```

Run it:
```bash
python okta_last_login_by_email.py --input emails.csv
```

**Arguments:**

| Argument | Required | Default | Description |
| --- | --- | --- | --- |
| `--input` | ✅ | — | Path to input CSV file containing email addresses |
| `--output` | | `okta_last_login_report_<YYYY-MM-DD>.csv` | Path to output CSV report (all users) |
| `--flagged-output` | | `okta_flagged_post_termination_logins_<YYYY-MM-DD>.csv` | Path to a CSV containing only flagged users. Only written when `--termination-column` is set |
| `--email-column` | | auto-detect | Column containing email addresses: a header name (e.g. `Email`) or a spreadsheet-style column letter (e.g. `D`) |
| `--termination-column` | | *(none)* | Column containing each user's termination/offboarding date: a header name or column letter (e.g. `C`). When set, flags any user whose last login is strictly after this date |
| `--event-types` | | `user.session.start` | Comma-separated Okta System Log `eventType`(s) to treat as a login |
| `--since` | | *(none)* | Only search log events on/after this ISO 8601 date (e.g. `2026-01-01`); narrows and speeds up the search |
| `--no-fallback` | | disabled | Don't fall back to the user profile's `lastLogin` field when no log event is found |

Examples:
```bash
# Basic lookup
python okta_last_login_by_email.py --input emails.csv

# Custom output path
python okta_last_login_by_email.py --input emails.csv --output report.csv

# Also count SSO logins as a "login"
python okta_last_login_by_email.py --input emails.csv --event-types "user.session.start,user.authentication.sso"

# Only search logs since a given date, and skip the profile fallback
python okta_last_login_by_email.py --input emails.csv --since 2026-01-01 --no-fallback

# Flag anyone who logged in after their termination date
# (email addresses in column D, termination dates in column C)
# Also produces okta_flagged_post_termination_logins_<date>.csv
python okta_last_login_by_email.py --input TCSUsers.csv --email-column D --termination-column C

# Custom path for the flagged-only file
python okta_last_login_by_email.py --input TCSUsers.csv --email-column D --termination-column C --flagged-output flagged.csv
```

Termination dates are parsed as `M/D/YYYY` (e.g. `5/27/2026`) first, with a few other common formats also
supported as fallbacks (`YYYY-MM-DD`, `YYYY/MM/DD`, `DD-Mon-YYYY`, `Month DD, YYYY`). A user is flagged only
if their last login is strictly **after** the termination date (i.e. on/after midnight UTC of the *next*
day) — a login on the termination date itself is not flagged.

## How It Works

1. Reads and de-duplicates email addresses (and, if `--termination-column` is set, each user's termination date) from the input CSV. Title/header rows are skipped automatically since they don't contain a real email address.
2. For each address, queries `GET /api/v1/logs` filtered by `actor.alternateId` and `eventType`, sorted descending with `limit=1`, to fetch only the single most recent matching event.
3. If a matching log event is found, its `published` timestamp is used as the last login.
4. If no log event is found (and `--no-fallback` isn't set), fetches the user via `GET /api/v1/users/{email}` and uses the profile's `lastLogin` field instead, noting that the value came from the profile rather than the log.
5. If the user doesn't exist in Okta, or has no login on record at all, that's noted in the report.
6. If a termination date was supplied for that user and a last login was found, compares the two and marks `logged_in_after_termination` as `YES` when the login is on/after midnight UTC of the day *following* the termination date.
7. If `--termination-column` was used, writes every row with `logged_in_after_termination = YES` to a second, flagged-only CSV.

## Error Handling

- Validates required environment variables are set
- Retries on 429 (rate limit) using the `x-rate-limit-reset`/`Retry-After` header, and on 5xx with exponential backoff
- Catches per-user HTTP/request errors so one failure doesn't stop the whole batch — the error is recorded in that user's report row instead

## Output

A CSV report (default `okta_last_login_report_<YYYY-MM-DD>.csv`) with one row per input email:

| Column | Description |
| --- | --- |
| `email` | The email address looked up |
| `okta_status` | The user's Okta status (only populated when the profile fallback is used) |
| `last_login_utc` | Timestamp of the last login found, if any |
| `source` | `system_log` or `profile_lastLogin`, indicating where the timestamp came from |
| `event_type` | The matched System Log `eventType`, when found via the log |
| `termination_date` | The termination date read from the input CSV for that user (blank if `--termination-column` wasn't used) |
| `logged_in_after_termination` | `YES` if the last login was strictly after the termination date, otherwise blank |
| `notes` | User-not-found, no-login-on-record, unparseable-termination-date, or error details |

When `--termination-column` is used, a second CSV (default `okta_flagged_post_termination_logins_<YYYY-MM-DD>.csv`) is also written with the same columns, containing only the rows flagged `YES` — a ready-made list for follow-up, and a ready-made input for `okta_deactivate_users_by_email.py`.

The script also prints per-user progress to the console as it runs, including a `*** FLAGGED ***` line for each post-termination login.

---

# `okta_deactivate_users_by_email.py`

## Purpose

This script deactivates the Okta accounts for a list of emails read from a CSV — the same input format as `okta_last_login_by_email.py` (header name or spreadsheet-style column letter for the email column, with title/header rows skipped automatically). It's intended to be pointed at the flagged-output CSV produced above, but works with any CSV of email addresses.

For each email, the user is looked up in Okta and deactivated via `POST /api/v1/users/{id}/lifecycle/deactivate`, which sets their status to `DEPROVISIONED`. This can be reversed later via the Okta admin console or the Users API (reactivation puts the account back into `PROVISIONED`, requiring a new activation flow) — it is not a permanent delete.

**This is a write operation.** It runs in **dry-run mode by default**, previewing what would be deactivated without changing anything. Passing `--execute` still requires you to type `yes` at an interactive confirmation prompt before anything is actually deactivated.

Key Features:
- Same flexible CSV/column handling as `okta_last_login_by_email.py`
- Dry-run by default; `--execute` plus a typed `yes` confirmation is required to make changes
- Skips users who are already deactivated (`status == DEPROVISIONED`) or don't exist in Okta
- Optional `--limit` to cap how many users are deactivated in a single run
- Implements retry/backoff on 429 rate limits and 5xx errors
- Writes a CSV audit report of what was (or would be) done for every user
- Exits with a non-zero status if any deactivation failed, so it plays nicely in scripts/pipelines

## Running the Script

Dry run (preview only, default):
```bash
python okta_deactivate_users_by_email.py --input flagged.csv
```

Actually deactivate (prompts for confirmation):
```bash
python okta_deactivate_users_by_email.py --input flagged.csv --execute
```

**Arguments:**

| Argument | Required | Default | Description |
| --- | --- | --- | --- |
| `--input` | ✅ | — | Path to input CSV file containing email addresses |
| `--email-column` | | auto-detect | Column containing email addresses: a header name (e.g. `Email`) or a spreadsheet-style column letter (e.g. `D`) |
| `--output` | | `okta_deactivation_report_<YYYY-MM-DD>.csv` | Path to output CSV audit report |
| `--execute` | | disabled (dry run) | Actually deactivate users; omit for a dry run |
| `--limit` | | *(none)* | Maximum number of users to deactivate in one run (optional safety cap) |

Examples:
```bash
# Preview what would be deactivated
python okta_deactivate_users_by_email.py --input flagged.csv

# Email addresses in column D of a raw offboarding sheet
python okta_deactivate_users_by_email.py --input TCSUsers.csv --email-column D

# Actually deactivate, capped at 10 users, with a custom report path
python okta_deactivate_users_by_email.py --input flagged.csv --execute --limit 10 --output deactivation_report.csv
```

## How It Works

1. Reads and de-duplicates email addresses from the input CSV, skipping title/header rows automatically.
2. If `--execute` is set, prints the full list of users to be deactivated and requires a typed `yes` confirmation before proceeding — anything else aborts with no changes made.
3. For each email, fetches the user via `GET /api/v1/users/{email}`.
   - Not found → skipped, noted in the report.
   - Already `DEPROVISIONED` → skipped, noted as "Already deactivated".
   - Otherwise → deactivated via `POST /api/v1/users/{id}/lifecycle/deactivate` (or, in dry-run mode, just recorded as "would deactivate").
4. Writes an audit CSV and prints a run summary (deactivated / skipped / failed counts).

## Error Handling

- Validates required environment variables are set
- Retries on 429 (rate limit) using the `x-rate-limit-reset`/`Retry-After` header, and on 5xx with exponential backoff
- Catches per-user HTTP/request errors so one failure doesn't stop the whole batch — the error is recorded in that user's report row instead, and the script exits non-zero if any user failed
- Requires an explicit `--execute` flag *and* a typed `yes` confirmation before making any changes

## Output

A CSV audit report (default `okta_deactivation_report_<YYYY-MM-DD>.csv`) with one row per input email:

| Column | Description |
| --- | --- |
| `email` | The email address processed |
| `okta_user_id` | The user's Okta ID (blank if not found) |
| `previous_status` | The user's Okta status before this run |
| `action` | `deactivated`, `would deactivate` (dry run), or `none` (skipped) |
| `result` | `success`, `dry-run`, `skipped`, or `failed` |
| `notes` | Reason for a skip, or error details |

# Notes

- Both scripts use the Okta API v1.
- System Log retention varies by Okta plan (often ~90 days) — the `lastLogin` fallback in `okta_last_login_by_email.py` covers logins older than that, but only reflects the single most recent login, not a full history.
- `okta_deactivate_users_by_email.py` is the only write-capable script here — always review its dry-run output (and the audit CSV) before passing `--execute`.
