#!/usr/bin/env python3
"""get_business_info.py --business demo-groomer --json

Returns the business's display name, address, timezone, services (display
name + duration), and opening hours. Use this for FAQ-style questions
(business name, address, opening hours, what services are offered) and to
get the exact business name for booking confirmations - never state these
from memory.
"""

import argparse
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))
from _client import call_api, print_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--business", required=True, dest="business_id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = call_api("GET", f"/businesses/{args.business_id}")
    print_json(result)


if __name__ == "__main__":
    main()
