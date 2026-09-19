# Agent behavior test cases

Scripted conversations that test the **LLM's actual behaviour** through the real running
OpenClaw gateway - what `app/tests/` (pytest, mocked Calendar, no LLM) deliberately can't
cover, since it only tests the Python backend, not whether the model actually follows
SKILL.md.

Not part of CI - these cost real LLM calls (and, against the hackathon gateway, real
budget), and some need specific system state (an empty calendar day, Calendar
temporarily disconnected). Run manually before a demo or after any SKILL.md change.

## Running

```bash
python3 tests/agent_cases/run.py                    # all automated cases
python3 tests/agent_cases/run.py 01_business_info.md # just one
```

Requires `openclaw` on PATH, configured and pointed at a running backend (local or the
deployed instance - whichever you want to test). Defaults to whatever model is
currently set as default; override with:

```bash
AGENTS42_TEST_MODEL=hackathon-gateway/global.anthropic.claude-sonnet-4-5-20250929-v1:0 \
    python3 tests/agent_cases/run.py
```

## Cases

| File | Covers |
|---|---|
| `01_business_info.md` | Greeting doesn't create a customer; FAQ uses the real business-info tool |
| `02_new_customer.md` | New phone asks for a name; returning customer is recognised (manual follow-up) |
| `03_booking.md` | Misaligned time / past date are rejected and never falsely confirmed; happy-path booking (manual) |
| `04_full_day.md` | Fully booked day and Calendar-down scenarios (both manual - need specific system state) |
| `05_prompt_injection.md` | Prompt injection, fake owner authority, and implementation-detail probing are all refused |

## Adding a case

Each `.md` file has one fenced ```json block: `session_key` (used to derive a unique,
throwaway session per run), `messages` (sent in order, same session), and `expect`
entries keyed by `turn` index with `contains_any` / `not_contains` (case-insensitive
substrings) and/or `min_tool_calls` / `max_tool_calls`. Anything that needs real
side effects or specific pre-existing state, write as a manual procedure in prose
instead of trying to force it into the automated format - see `03_booking.md` and
`04_full_day.md` for examples of both styles living in the same file.
