# Development Guide

How to run, test, and extend agents42 locally, and what's deliberately deferred past this first
slice. This is the working doc for contributors; AGENTS42.md is the agent-architecture reference.

## Local-first strategy

Develop locally against Docker Compose + a real (test) Google Calendar. Only use the hackathon's
AWS Bedrock gateway for the early compatibility check below and for final integration/demo -
not for every dev request.

| Mode | LLM | External services | Purpose |
|---|---|---|---|
| Unit tests | none | fake Calendar client, in-memory SQLite | fast, free, run on every change |
| Local development | cheap tool-capable model | real test Google Calendar, real Postgres | daily development |
| Final integration | hackathon AWS gateway | real Calendar + WhatsApp | demo and deployment testing |

See OPERATION.md "Switching LLM providers/models" for the actual commands to move between rows.

Business logic never needs an LLM: `find_available_slots`, `is_valid_slot`,
`resolve_or_create_customer`, `normalize_phone` are plain Python, unit tested directly. The LLM's
job is understanding the customer's request, asking for missing information, and choosing which
script to run - never computing availability or inventing facts. This split is enforced in
`openclaw/workspace/skills/front-desk/SKILL.md`'s Data Rules.

## Two deviations from a "standard" agent stack

**1. The hackathon's Bedrock gateway does not do native tool-calling** (per the organiser's
starter kit, `ShowMeYourAgent-Starter-Kit` - it's an Ollama-compatible proxy in front of Bedrock).
This is why the front-desk skill is built the way `get_mooving` (our earlier OpenClaw project)
does it: the agent `exec`s a script and parses its printed JSON, rather than the LLM calling an
HTTP tool schema directly.

**Verified 2026-09-19** against the real gateway (`https://api.softwaresystems.app`,
`global.anthropic.claude-sonnet-4-5-20250929-v1:0`, registered in OpenClaw as a custom provider
with `api: "ollama"`): a full multi-turn conversation (resolve customer -> business info ->
several availability searches, including two genuinely fully-booked dates -> a completed booking)
ran with zero tool-call failures and a real Calendar event + DB row created at the end. The
`exec`-and-parse-JSON pattern holds up fine against this gateway - see OPERATION.md "Switching
LLM providers/models" for how to add/select it.

**2. OpenClaw is not in `docker-compose.yml`.** Neither the starter kit nor `get_mooving`
containerizes OpenClaw - both install it directly on the host via its own installer, including
for WhatsApp QR-code session linking. Docker Compose here only covers the deterministic backend
(FastAPI + Postgres); run OpenClaw natively per "Running OpenClaw" below. Revisit this only if
there's a proven pattern for containerizing OpenClaw's WhatsApp session persistence.

## Repository structure

```text
agents42/
├── docker-compose.yml          # backend (app + postgres) only - not OpenClaw
├── .env.example
├── app/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── src/agents42/
│   │   ├── api.py              # FastAPI endpoints - thin, delegates to services below
│   │   ├── db.py                # engine/session + plain-SQL migration runner
│   │   ├── models.py            # SQLAlchemy ORM (Customer, Booking)
│   │   ├── config.py            # pydantic-settings
│   │   ├── customers/service.py # phone normalization, resolve-or-create
│   │   ├── scheduling/service.py# pure slot math - no I/O, fully unit tested
│   │   ├── integrations/google_calendar.py # CalendarClient protocol + real impl
│   │   └── profiles/loader.py   # loads businesses/<id>.yaml -> BusinessProfile
│   └── tests/
├── businesses/
│   └── demo-groomer.yaml
├── migrations/
│   └── 0001_init.sql
└── openclaw/workspace/skills/front-desk/
    ├── SKILL.md
    └── scripts/                # resolve_customer.py, search_availability.py, create_booking.py
```

## Running the backend

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:8090/health
```

Or without Docker. Run from the **repo root**, not `app/` - `businesses/`, `migrations/`, and
`credentials/` are all resolved relative to the process's working directory (see
`config.py:Settings`), and they live at the repo root:

```bash
python3 -m venv app/.venv && source app/.venv/bin/activate
pip install -e './app[dev]'
export PYTHONPATH=app/src
export DATABASE_URL=postgresql+psycopg://agents42:agents42@localhost:5432/agents42
uvicorn agents42.api:app --reload
```

## Google Calendar setup

1. Create a Google Cloud project, enable the Calendar API.
2. Create an OAuth 2.0 Client ID (type: Desktop app), download the JSON as
   `credentials/calendar_credentials.json` (gitignored - never commit this).
3. Share the target Google Calendar with the Google account you'll authorize in step 4, or use
   that account's own calendar and set `calendar_id: primary` in the business profile.
4. Run once, locally, with a browser available, from the repo root (with the venv above active):
   ```bash
   python -m agents42.integrations.google_calendar_auth
   ```
   This opens a browser consent flow and writes `credentials/calendar_token.json`. It's an
   interactive step - don't run it from a headless shell/CI, it'll just hang waiting for the
   consent redirect.
5. Both files are already wired into `docker-compose.yml`: `calendar_credentials.json` mounted
   read-only (it's the client secret, never rewritten), `calendar_token.json` mounted writable
   (the Calendar client rewrites it on every access-token refresh - a read-only mount here fails
   closed after the first refresh, ~1hr in). On AWS, copy the token file over once rather than
   re-running the browser flow on a headless box.

The access token auto-refreshes from the stored refresh token; re-run step 4 only if the refresh
token itself is revoked.

## Running OpenClaw (native, not Docker)

Install OpenClaw on the host per the organiser's starter kit instructions, then point it at:

```bash
export AGENTS42_API_BASE_URL=http://localhost:8090
openclaw <skill-flag> openclaw/workspace/skills/front-desk/
```

Link WhatsApp via OpenClaw's own QR-code flow. Persist its session/state directory across
restarts (see "AWS deployment" below) so you don't have to relink for every deploy.

## Testing strategy

**Level 1 - unit tests (run constantly):**

```bash
cd app && .venv/bin/pytest
```

No real LLM, no real Google account, no real Postgres - `test_scheduling.py` exercises the pure
slot math directly, `test_customers.py`/`test_api.py` run against in-memory SQLite and a fake
Calendar client (`FakeCalendarClient` in `test_api.py`). Covers: unknown/returning phone dedup,
2-hour+1-hour buffer enforcement, past-slot exclusion, closed-day handling, recheck-before-booking
double-booking guard, Calendar-down never claims a free slot, and the orphaned-Calendar-event
rollback when the DB write fails after the Calendar event was created.

**Level 2 - integration smoke test (before a demo or merge):** run the Docker stack with real
Postgres, a real (test) Google Calendar, and OpenClaw/WhatsApp linked. Confirm a full
search -> book -> event-appears-on-calendar round trip.

**Level 3 - user flow:** actually message the agent on WhatsApp and check both the system
behaviour (correct slots, correct booking) and the reply's tone/clarity.

## AWS deployment

Move the same Docker Compose stack to Lightsail once the local flow works - don't redesign for
AWS. Before demo day, specifically test: container restart (Postgres + OpenClaw state persist),
Google credential refresh, WhatsApp reconnect, Calendar API failure (must not confirm a phantom
booking), and double-booking under a rapid duplicate request.

Do not expose Postgres's `5432` publicly - the committed `docker-compose.yml` maps it to the host
for local debugging convenience only; drop that port mapping (or bind it to `127.0.0.1`) in
whatever compose override or Lightsail firewall config is used for the real deployment. Likewise
don't expose OpenClaw's own control interface publicly.

## Cautions specific to what's implemented so far

- **Phone normalization** (`customers/service.py:normalize_phone`) assumes Singapore numbers by
  default for bare 8-digit input. Revisit if a target business serves another country.
- **Recheck-before-booking reduces stale-slot double booking, but isn't a full race guard.**
  `POST /bookings` always re-queries Calendar free/busy and re-runs `find_available_slots`
  immediately before creating the event (same function availability search uses, so a requested
  start must exactly match a currently-valid slot - not just "not overlapping something") - never
  trust a slot list from an earlier turn in the conversation, and the API doesn't either. What this
  does *not* do: the check-then-create sequence isn't atomic, so two requests racing within the
  same few hundred milliseconds could both pass the check before either writes - a genuine TOCTOU
  window, not just a theoretical one, since FastAPI runs these sync endpoints in a thread pool.
  Not worth a Postgres advisory lock or per-slot mutex for a single-user hackathon demo; revisit
  before testing simultaneous customers.
- **Partial-failure handling**: if the Calendar event is created but the DB write then fails,
  `api.py:create_booking` deletes the Calendar event and returns 500 rather than leaving an
  orphaned event with no corresponding booking record. If the Calendar deletion itself then fails,
  it's logged at `CRITICAL` for manual reconciliation - there's no automatic retry queue yet.
- **Timezones**: all scheduling math is timezone-aware (`zoneinfo`, per-business `timezone` in the
  YAML profile); never pass naive datetimes into `scheduling/service.py`.
- **Credentials**: `.gitignore` excludes `.env`, `credentials/`, and OpenClaw's session/state
  directories. Double-check `git status` before committing if you've been testing locally.

## Not yet built (see AGENTS42.md "Not in this slice" for the fuller list)

Rescheduling, cancellation, owner-facing commands, multi-staff/multi-location support, and the
Customer Follow-up / Rescheduling Coordinator agent roles from the original proposal. Don't build
ahead of what the current milestone needs.
