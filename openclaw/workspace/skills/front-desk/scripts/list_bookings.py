#!/usr/bin/env python3
"""list_bookings.py --business <business_id> --customer_id <uuid> --json

Returns the customer's upcoming, confirmed bookings *for this business only*
- a customer can have bookings with other agents42 businesses too (same
phone, same customer_id everywhere), but this WhatsApp session is fixed to
one business, so that's all this ever returns or should ever be shown.
Empty list means no bookings to check on/reschedule/cancel here - don't
invent one. Use this before reschedule_booking.py or cancel_booking.py to
find out which booking the customer means.
"""

import argparse
from pathlib import Path
from urllib.parse import urlencode

import sys

sys.path.insert(0, str(Path(__file__).parent))
from _client import call_api, print_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--business", required=True, dest="business_id")
    parser.add_argument("--customer_id", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    query = urlencode({"business_id": args.business_id})
    result = call_api("GET", f"/customers/{args.customer_id}/bookings?{query}")
    print_json(result)


if __name__ == "__main__":
    main()
