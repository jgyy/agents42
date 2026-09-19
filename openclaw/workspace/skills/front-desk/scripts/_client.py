"""Shared helper for the front-desk skill's CLI scripts.

Deliberately stdlib-only (urllib, json) - these scripts run on whatever host
has OpenClaw installed, and should not require a separate pip install just
to talk to the agents42 API over HTTP.
"""

import json
import os
import sys
import urllib.error
import urllib.request

API_BASE_URL = os.environ.get("AGENTS42_API_BASE_URL", "http://localhost:8090")


def call_api(method: str, path: str, payload: dict | None = None) -> dict:
    """POST/GET the agents42 API and return its parsed JSON body.

    On any failure (network error, non-2xx status, bad JSON) returns a dict
    with an "error" key instead of raising - callers print this straight to
    stdout as the script's JSON output, so the agent sees a clear error
    field rather than a stack trace or nothing at all.
    """
    url = f"{API_BASE_URL}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail", exc.reason)
        except Exception:
            detail = exc.reason
        return {"error": "http_error", "status": exc.code, "detail": detail}
    except urllib.error.URLError as exc:
        return {"error": "connection_failed", "detail": str(exc.reason)}
    except Exception as exc:  # malformed JSON, timeout, etc.
        return {"error": "unexpected_failure", "detail": str(exc)}


def print_json(result: dict) -> None:
    print(json.dumps(result, indent=2))
    if "error" in result:
        sys.exit(1)
