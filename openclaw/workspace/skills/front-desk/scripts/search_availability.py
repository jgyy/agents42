#!/usr/bin/env python3
"""search_availability.py --business demo-groomer --service full_grooming --date 2026-09-18 [--period afternoon] --json

Returns the real available slots for that business/service/date, already
excluding existing calendar bookings and applying the service's required
buffer. Never compute or guess slots yourself - only relay what this
returns.
"""

import argparse
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))
from _client import call_api, print_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--business", required=True, dest="business_id")
    parser.add_argument("--service", required=True)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--period", choices=["morning", "afternoon", "evening"], default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = {"business_id": args.business_id, "service": args.service, "date": args.date}
    if args.period:
        payload["period"] = args.period

    result = call_api("POST", "/availability/search", payload)
    print_json(result)


if __name__ == "__main__":
    main()
