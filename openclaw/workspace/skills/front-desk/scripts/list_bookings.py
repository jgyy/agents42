#!/usr/bin/env python3
"""list_bookings.py --customer_id <uuid> --json

Returns the customer's upcoming, confirmed bookings (across all businesses,
though in practice always this business's own). Empty list means no
bookings to reschedule - don't invent one. Use this before reschedule_booking.py
to find out which booking the customer means.
"""

import argparse
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))
from _client import call_api, print_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer_id", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = call_api("GET", f"/customers/{args.customer_id}/bookings")
    print_json(result)


if __name__ == "__main__":
    main()
