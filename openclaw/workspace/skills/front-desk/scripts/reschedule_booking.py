#!/usr/bin/env python3
"""reschedule_booking.py --booking_id <uuid> --customer_id <uuid> --new_start 2026-09-27T14:00:00+08:00 --json

Moves an existing confirmed booking to a new time (same service, same
booking id - not a new booking). Rechecks availability itself immediately
before moving it. Only report success if this returns "status": "confirmed"
- any other outcome, including "slot_unavailable", means nothing changed
and the original booking is still in effect at its original time.
"""

import argparse
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))
from _client import call_api, print_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--booking_id", required=True)
    parser.add_argument("--customer_id", required=True)
    parser.add_argument("--new_start", required=True, help="ISO 8601, e.g. 2026-09-27T14:00:00+08:00")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = {"customer_id": args.customer_id, "new_start": args.new_start}

    result = call_api("POST", f"/bookings/{args.booking_id}/reschedule", payload)
    if result.get("error") == "http_error" and result.get("status") == 409:
        result = {"error": "slot_unavailable", "detail": result.get("detail")}

    print_json(result)


if __name__ == "__main__":
    main()
