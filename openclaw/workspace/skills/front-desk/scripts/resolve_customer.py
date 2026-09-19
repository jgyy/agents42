#!/usr/bin/env python3
"""resolve_customer.py --phone "+6591234567" [--name "Sarah Tan"] --json

Finds or creates the customer by phone number. If the phone is new and
--name is omitted, this returns {"needs_name": true} rather than an error -
ask the customer for their name and run again with --name to create them.
"""

import argparse
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))
from _client import call_api, print_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument("--json", action="store_true", help="present for symmetry with other scripts; output is always JSON")
    args = parser.parse_args()

    payload = {"phone": args.phone}
    if args.name:
        payload["name"] = args.name

    result = call_api("POST", "/customers/resolve", payload)
    print_json(result)


if __name__ == "__main__":
    main()
