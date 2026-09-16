#!/usr/bin/env python3
"""create_booking.py --business demo-groomer --customer_id <uuid> --service full_grooming --start 2026-09-18T13:00:00+08:00 --json

Rechecks availability and creates the booking (Calendar event + DB record)
in one atomic step. Only report a booking as confirmed if this returns
"status": "confirmed" - any other outcome, including "slot_unavailable",
means nothing was booked.
"""

import argparse
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))
from _client import call_api, print_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--business", required=True, dest="business_id")
    parser.add_argument("--customer_id", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--start", required=True, help="ISO 8601, e.g. 2026-09-18T13:00:00+08:00")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = {
        "business_id": args.business_id,
        "customer_id": args.customer_id,
        "service": args.service,
        "start": args.start,
    }

    result = call_api("POST", "/bookings", payload)
    if result.get("error") == "http_error" and result.get("status") == 409:
        result = {"error": "slot_unavailable", "detail": result.get("detail")}

    print_json(result)


if __name__ == "__main__":
    main()
