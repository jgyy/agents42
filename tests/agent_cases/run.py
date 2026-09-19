#!/usr/bin/env python3
"""Lightweight runner for the front-desk skill's behavioral test cases.

This is deliberately not a "fancy evaluation platform" - each case is a
markdown file with a fenced ```json block describing a short conversation
and some substring/tool-call checks on each turn's response. It drives the
*real* running OpenClaw gateway (local or remote, whichever `openclaw` on
PATH is configured for) via `openclaw agent`, so it exercises actual LLM
behaviour, not just the Python backend - that's the gap pytest can't cover.

Costs real LLM calls (and, if pointed at the hackathon gateway, real budget)
- not part of CI, run manually:

    python3 tests/agent_cases/run.py                    # all cases
    python3 tests/agent_cases/run.py 01_business_info.md # one case
    AGENTS42_TEST_MODEL=hackathon-gateway/global.anthropic.claude-sonnet-4-5-20250929-v1:0 \\
        python3 tests/agent_cases/run.py                # against a specific model

Each case gets a fresh, uniquely-suffixed session key so runs don't
interfere with each other or with real conversations.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

CASES_DIR = Path(__file__).parent
JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def load_case(path: Path) -> dict:
    text = path.read_text()
    match = JSON_BLOCK_RE.search(text)
    if not match:
        raise ValueError(f"{path}: no ```json block found")
    return json.loads(match.group(1))


def run_turn(session_key: str, message: str) -> dict:
    cmd = ["openclaw", "agent", "--session-key", session_key, "--json", "-m", message]
    model = os.environ.get("AGENTS42_TEST_MODEL")
    if model:
        cmd[2:2] = ["--model", model]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    # openclaw prints occasional locale warnings to stdout before the JSON -
    # find the first '{' and parse from there.
    stdout = result.stdout
    start = stdout.find("{")
    if start == -1:
        raise RuntimeError(f"No JSON in openclaw output:\n{stdout}\n{result.stderr}")
    return json.loads(stdout[start:])


def check_turn(turn_spec: dict, response_text: str, tool_calls: int) -> list[str]:
    failures = []
    lower = response_text.lower()

    for phrase in turn_spec.get("contains_any", []):
        if phrase.lower() not in lower:
            continue
        break
    else:
        if turn_spec.get("contains_any"):
            failures.append(f"expected one of {turn_spec['contains_any']!r} in response, found none")

    for phrase in turn_spec.get("not_contains", []):
        if phrase.lower() in lower:
            failures.append(f"response should not contain {phrase!r}, but it did")

    min_calls = turn_spec.get("min_tool_calls")
    if min_calls is not None and tool_calls < min_calls:
        failures.append(f"expected >= {min_calls} tool call(s), got {tool_calls}")

    max_calls = turn_spec.get("max_tool_calls")
    if max_calls is not None and tool_calls > max_calls:
        failures.append(f"expected <= {max_calls} tool call(s), got {tool_calls}")

    return failures


def run_case(path: Path) -> bool:
    case = load_case(path)
    session_key = f"agent:main:testcase-{case['session_key']}-{int(time.time())}"
    print(f"\n=== {path.name}: {case.get('description', '')} ===")

    ok = True
    for i, message in enumerate(case["messages"]):
        print(f"  turn {i}: {message!r}")
        try:
            result = run_turn(session_key, message)
        except Exception as exc:
            print(f"    FAIL: run error: {exc}")
            ok = False
            continue

        payloads = result.get("result", {}).get("payloads", [])
        response_text = payloads[0]["text"] if payloads else ""
        tool_calls = result.get("result", {}).get("meta", {}).get("toolSummary", {}).get("calls", 0)
        print(f"    reply: {response_text[:200]!r}")

        turn_specs = [e for e in case.get("expect", []) if e.get("turn") == i]
        for spec in turn_specs:
            failures = check_turn(spec, response_text, tool_calls)
            for f in failures:
                print(f"    FAIL: {f}")
                ok = False
            if not failures and spec:
                print("    ok")

    return ok


def main() -> None:
    args = sys.argv[1:]
    case_files = sorted(CASES_DIR.glob("*.md")) if not args else [CASES_DIR / a for a in args]

    results = {}
    for path in case_files:
        results[path.name] = run_case(path)

    print("\n=== Summary ===")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
