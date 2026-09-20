#!/usr/bin/env python3
"""flag_attention.py --business <business_id> [--customer_id <uuid>] [--booking_id <uuid>] --reason <short_reason> [--detail "<text>"] --json

Records an escalation for the owner dashboard's Attention panel. Best-effort:
a failure here does not change what you tell the customer - they already
hear "the business will follow up" regardless of whether this call succeeds.
"""

import argparse
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))
from _client import call_api, print_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--business", required=True, dest="business_id")
    parser.add_argument("--customer_id")
    parser.add_argument("--booking_id")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--detail")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = {"business_id": args.business_id, "reason": args.reason}
    if args.customer_id:
        payload["customer_id"] = args.customer_id
    if args.booking_id:
        payload["booking_id"] = args.booking_id
    if args.detail:
        payload["detail"] = args.detail

    print_json(call_api("POST", "/escalations", payload))


if __name__ == "__main__":
    main()
