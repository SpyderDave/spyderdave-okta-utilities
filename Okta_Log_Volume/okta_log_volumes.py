"""
Count Okta System Log events and bytes per day for the last 30 days,
project daily/weekly/monthly/yearly volume, and estimate the equivalent
Dynatrace Log Management & Analytics ingest volume/cost if streamed via
the Dynatrace "Okta Audit Logs" extension.

Hard-coded window: 2026-07-20T00:00:00.000Z -> 2026-08-19T00:00:00.000Z

Configuration is read from environment variables:
    OKTA_ORG_URL    Your Okta org URL, e.g. "https://acme.okta.com"
    OKTA_API_TOKEN  Your Okta API token

Set them, then run:
    pip install requests
    python okta_log_volumes.py

Bash:
    export OKTA_ORG_URL="https://acme.okta.com"
    export OKTA_API_TOKEN="00your_token_here"

PowerShell:
    $env:OKTA_ORG_URL = "https://acme.okta.com"
    $env:OKTA_API_TOKEN = "00your_token_here"
"""

import os
import sys
import requests
import time
from datetime import datetime, timedelta

OKTA_ORG_URL = os.environ.get("OKTA_ORG_URL", "").rstrip("/")
OKTA_API_TOKEN = os.environ.get("OKTA_API_TOKEN", "")

if not OKTA_ORG_URL or not OKTA_API_TOKEN:
    sys.exit(
        "ERROR: OKTA_ORG_URL and OKTA_API_TOKEN environment variables must be set.\n"
        "  Bash:       export OKTA_ORG_URL=\"https://acme.okta.com\"; export OKTA_API_TOKEN=\"00your_token_here\"\n"
        "  PowerShell: $env:OKTA_ORG_URL=\"https://acme.okta.com\"; $env:OKTA_API_TOKEN=\"00your_token_here\""
    )

BASE = f"{OKTA_ORG_URL}/api/v1/logs"
HEADERS = {"Authorization": f"SSWS {OKTA_API_TOKEN}"}

# Hard-coded 30-day window
WINDOW_END = datetime(2026, 8, 19, 0, 0, 0)
WINDOW_START = WINDOW_END - timedelta(days=30)

# Dynatrace Log Management & Analytics - Ingest & Process is billed on raw
# bytes sent to Dynatrace *before* enrichment/transformation, per GiB.
# The Okta Audit Logs extension forwards System Log events largely as-is
# via the Log Ingestion API, so raw Okta JSON volume is a reasonable proxy
# for Dynatrace ingest volume. $0.20/GiB is a commonly published list price
# for Ingest & Process (DPS) -- confirm your actual contracted rate.
DYNATRACE_PRICE_PER_GIB = 0.20
DYNATRACE_OVERHEAD_FACTOR = 1.0  # bump above 1.0 if you expect extension metadata overhead


def count_events(since: str, until: str) -> tuple[int, int]:
    """Returns (event_count, byte_count) for the given window."""
    url = f"{BASE}?since={since}&until={until}&limit=1000"
    total_events = 0
    total_bytes = 0
    while url:
        r = requests.get(url, headers=HEADERS)
        if r.status_code == 429:
            wait = int(r.headers.get("x-rate-limit-reset", 30))
            print(f"  Rate limited, waiting {wait}s...")
            time.sleep(wait + 1)
            continue
        r.raise_for_status()
        events = r.json()
        total_events += len(events)
        total_bytes += len(r.content)  # raw payload size for this page
        url = r.links.get("next", {}).get("url")
    return total_events, total_bytes


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def dynatrace_gib_and_cost(byte_count: float) -> tuple[float, float]:
    dt_bytes = byte_count * DYNATRACE_OVERHEAD_FACTOR
    gib = dt_bytes / (1024 ** 3)
    return gib, gib * DYNATRACE_PRICE_PER_GIB


def main():
    print(f"Counting Okta System Log events from {iso(WINDOW_START)} to {iso(WINDOW_END)}\n")

    daily_stats = []
    day = WINDOW_START
    while day < WINDOW_END:
        next_day = day + timedelta(days=1)
        events, byte_count = count_events(iso(day), iso(next_day))
        daily_stats.append((day.strftime("%Y-%m-%d"), events, byte_count))
        print(f"{day.strftime('%Y-%m-%d')}: {events} events, {human_bytes(byte_count)}")
        day = next_day

    total_events = sum(e for _, e, _ in daily_stats)
    total_bytes = sum(b for _, _, b in daily_stats)
    n_days = len(daily_stats) or 1
    avg_events_per_day = total_events / n_days
    avg_bytes_per_day = total_bytes / n_days

    print("\n--- Summary (last 30 days) ---")
    print(f"Total events: {total_events}  |  Total volume: {human_bytes(total_bytes)}")
    print(f"Avg/day:      {avg_events_per_day:.1f} events  |  {human_bytes(avg_bytes_per_day)}")
    print(f"Projected weekly:  {avg_events_per_day * 7:.0f} events  |  {human_bytes(avg_bytes_per_day * 7)}")
    print(f"Projected monthly: {avg_events_per_day * 30:.0f} events  |  {human_bytes(avg_bytes_per_day * 30)}")
    print(f"Projected yearly:  {avg_events_per_day * 365:.0f} events  |  {human_bytes(avg_bytes_per_day * 365)}")

    print("\n--- Dynatrace ingest estimate (Okta Audit Logs extension) ---")
    print(f"(assumes ~1:1 forwarding of raw event bytes; overhead factor = {DYNATRACE_OVERHEAD_FACTOR}, "
          f"rate = ${DYNATRACE_PRICE_PER_GIB:.2f}/GiB -- verify against your contract)")
    for label, bytes_val in (
        ("Daily", avg_bytes_per_day),
        ("Weekly", avg_bytes_per_day * 7),
        ("Monthly", avg_bytes_per_day * 30),
        ("Yearly", avg_bytes_per_day * 365),
    ):
        gib, cost = dynatrace_gib_and_cost(bytes_val)
        print(f"{label:8s}: {human_bytes(bytes_val):>10s}  ({gib:.4f} GiB)  ~${cost:.4f}")


if __name__ == "__main__":
    main()