#!/usr/bin/env python3
"""search_availability.py --business demo-groomer --service full_grooming --date 2026-09-18 [--period afternoon] [--exclude_booking_id <uuid> --customer_id <uuid>] --json

Returns the real available slots for that business/service/date, already
excluding existing calendar bookings and applying the service's required
buffer. Never compute or guess slots yourself - only relay what this
returns.

When searching for a *reschedule*, pass --exclude_booking_id (the booking
being moved) together with --customer_id: that booking's own calendar hold
is then ignored, so times overlapping its current slot ("push it back an
hour") are offered when they're genuinely free. Without it, search treats
the customer's own booking as a conflict and those slots never appear.
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
    parser.add_argument("--exclude_booking_id", default=None, help="booking being rescheduled; requires --customer_id")
    parser.add_argument("--customer_id", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = {"business_id": args.business_id, "service": args.service, "date": args.date}
    if args.period:
        payload["period"] = args.period
    if args.exclude_booking_id:
        if not args.customer_id:
            parser.error("--exclude_booking_id requires --customer_id")
        payload["exclude_booking_id"] = args.exclude_booking_id
        payload["customer_id"] = args.customer_id

    result = call_api("POST", "/availability/search", payload)
    print_json(result)


if __name__ == "__main__":
    main()
