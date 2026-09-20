#!/usr/bin/env python3
"""cancel_booking.py --business <business_id> --booking_id <uuid> --customer_id <uuid> --json

Cancels an existing confirmed booking (same booking id, marked cancelled -
not deleted, and its Calendar event is removed). --business must be this
session's own business - the backend rejects a booking that belongs to a
different business even for the same customer. Only report the booking as
cancelled if this returns "status": "cancelled" - any other outcome means
nothing changed and the booking is still in effect.
"""

import argparse
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))
from _client import call_api, print_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--business", required=True, dest="business_id")
    parser.add_argument("--booking_id", required=True)
    parser.add_argument("--customer_id", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = {"customer_id": args.customer_id, "business_id": args.business_id}

    result = call_api("POST", f"/bookings/{args.booking_id}/cancel", payload)
    print_json(result)


if __name__ == "__main__":
    main()
