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
├── docker-compose.yml          # backend (app + owner-dashboard + postgres) - not OpenClaw
├── .env.example
├── app/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── src/agents42/
│   │   ├── api.py               # customer-facing FastAPI endpoints - thin, delegates to services below
│   │   ├── owner_api.py         # owner dashboard - separate app, reuses api.py's core logic
│   │   ├── templates/dashboard.html
│   │   ├── db.py                # engine/session + plain-SQL migration runner
│   │   ├── models.py            # SQLAlchemy ORM (Customer, Booking, Escalation, BlockedSlot)
│   │   ├── config.py            # pydantic-settings
│   │   ├── customers/service.py # phone normalization, resolve-or-create
│   │   ├── scheduling/service.py# pure slot math - no I/O, fully unit tested
│   │   ├── integrations/google_calendar.py # CalendarClient protocol + real impl
│   │   └── profiles/loader.py   # loads businesses/<id>.yaml -> BusinessProfile
│   └── tests/
├── businesses/
│   └── demo-groomer.yaml
├── migrations/
│   ├── 0001_init.sql
│   └── 0002_escalations_and_blocked_slots.sql
└── openclaw/workspace/skills/front-desk/
    ├── SKILL.md
    └── scripts/                # resolve_customer.py, search_availability.py, create_booking.py, ...
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
   GOOGLE_CALENDAR_CREDENTIALS_PATH=credentials/calendar_credentials.json \
   GOOGLE_CALENDAR_TOKEN_PATH=credentials/calendar_token.json \
   python -m agents42.integrations.google_calendar_auth
   ```
   **The env var overrides are required, not optional, if you already have a `.env` file** (you
   will, once you've done the Docker setup above) - `.env` sets these two paths to the
   *container* paths (`/app/credentials/...`) for `docker-compose.yml`'s benefit, and
   `Settings` reads `.env` unconditionally regardless of whether you're running in Docker or
   directly on the host, so running this script without the override looks for the credential
   file at a path that only exists inside the container and fails with a confusing "missing OAuth
   client secret" error even though the file is right there in `credentials/`.
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

**A real way this happens**: enabling or changing 2-Step Verification on
the authorizing Google account silently revokes its existing OAuth grants, including this one -
hit in production when 2-Step Verification was turned on for the same account used for Calendar,
in order to generate a Gmail App Password for owner escalation emails (see "Owner escalation
emails" below). Both `get_busy_periods`/`create_event`/`delete_event` now translate the resulting
`google.auth.exceptions.RefreshError` into a normal `CalendarError` (502 "Calendar unavailable"),
same as any other Calendar failure - but the underlying access is still actually broken until
step 4 is re-run and the fresh token copied to every deployment using it (local + AWS). If
Calendar starts failing right after a security-settings change on that account, this is the
first thing to check, not a code bug.

## Running OpenClaw (native, not Docker)

Install OpenClaw on the host per the organiser's starter kit instructions, then point it at:

```bash
export AGENTS42_API_BASE_URL=http://localhost:8090
openclaw <skill-flag> openclaw/workspace/skills/front-desk/
```

Link WhatsApp via OpenClaw's own QR-code flow. Persist its session/state directory across
restarts (see "AWS deployment" below) so you don't have to relink for every deploy.

## Owner dashboard

A small server-rendered dashboard for the business owner, one page with five anchor-linked
sections: **Overview** (stat counts + the "Attention" queue of escalations, kept near the top
deliberately - what needs the owner's action matters more than a history log), **Schedule**
(today's/upcoming bookings with customer details inline, manual reschedule/cancel, checking
availability, blocking off unavailable time), **Customers** (searchable directory - name/phone,
booking count, last booking date - each linking to a detail page with full booking history),
**Business** (read-only view of the same business profile - name, address, hours, services - the
front-desk agent itself uses, so the owner can sanity-check "what does my AI currently believe
about my business" without asking a developer), and **Activity** (recently cancelled/changed
bookings and resolved-escalation history - background context, separate from the Attention queue).
Escalations get onto the Attention queue via `flag_attention.py` (see below). No model is
involved anywhere in the dashboard - see AGENTS42.md "Owner dashboard" for how this relates to
the Owner Assistant Agent role, which isn't built yet.

Runs as its own service (`agents42.owner_api:app`), reusing the same built image as `app` (see
`docker-compose.yml`) - it shares `api.py`'s models, session, Calendar client, and (notably) the
reschedule/cancel Calendar+DB compensation logic directly, rather than reimplementing it.

**Editing the business profile is deliberately not built yet** - the Business section is
read-only, showing exactly what's in `businesses/<id>.yaml`. Changing hours/services/pricing still
needs a developer to edit that file and redeploy. The plan is to add editing as a second step once
the read-only view has been used for a while, with server-side validation (never letting the
dashboard write YAML directly from an HTML form) - editing this data affects live scheduling
logic, so it deserves more care than the mostly-CRUD booking/customer views above.

```bash
cp .env.example .env    # set OWNER_DASHBOARD_PASSWORD and OWNER_DASHBOARD_BUSINESS_ID
docker compose up -d --build
curl -u <any-username>:$OWNER_DASHBOARD_PASSWORD http://localhost:8091/health
```

Or without Docker, from the repo root with the venv active (same `PYTHONPATH`/cwd requirements as
the main app):

```bash
uvicorn agents42.owner_api:app --port 8091
```

**Opening it in a browser (local dev)**: go to `http://localhost:8091/` - the browser will prompt
for a username/password; any username works, the password is whatever `OWNER_DASHBOARD_PASSWORD`
is set to in `.env`.

**On the deployed AWS instance, there are two ways to reach it** - pick whichever fits:

1. **Direct HTTP** (what's set up now): `http://<instance-static-ip>:8091/` - ask a developer for
   the IP and password (not published here, this repo is public). Requires the one-time Lightsail
   firewall rule opening port 8091 (see DEPLOYMENT.md) - without it, this times out rather than
   reaching the server at all. This is the plain-HTTP path with the documented limitations above
   (no TLS, credentials and customer PII travel unencrypted) - fine for a demo, not for anything
   beyond it.
2. **SSH tunnel** (no firewall rule needed, encrypted end-to-end via SSH instead of Basic Auth
   over plain HTTP): from a machine with SSH access to the instance (see DEPLOYMENT.md "Giving a
   teammate SSH access"),
   ```bash
   ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem -L 8091:localhost:8091 ubuntu@<instance-static-ip>
   ```
   then open `http://localhost:8091/` in a browser on *your own* machine - the browser still
   prompts for the dashboard's Basic Auth password (the tunnel doesn't replace that, it just
   avoids exposing the port publicly at all). Keep the SSH session open for as long as you want
   the tunnel to work.

**Auth**: HTTP Basic, single shared password, any username - matches the product decision (one
owner, not per-user accounts). The server refuses to start at all if
`OWNER_DASHBOARD_PASSWORD`/`OWNER_DASHBOARD_BUSINESS_ID` aren't set, and every route except
`/health` requires the password - an unset password means "always reject," never "no auth
needed."

**Known, documented limitations** (not silently accepted - revisit before any real production use
beyond the hackathon):
- **Plain HTTP, no TLS.** Credentials and customer PII travel unencrypted to whoever's on the
  network path. No domain name is available for this deployment to get a real Let's Encrypt
  certificate; a self-signed cert or restricting the Lightsail firewall rule's source IP (see
  DEPLOYMENT.md) are the cheap mitigations if this matters more later.
- **"Recently cancelled / changed" is a heuristic** (`updated_at != created_at`, or status
  `cancelled`) - there's no dedicated reschedule-history table behind it.
- Only supports one business per deployment (`OWNER_DASHBOARD_BUSINESS_ID` is a single fixed
  value), matching this project's existing "one business per deployment" scope generally.

(CSRF protection on the dashboard's state-changing routes *is* built - `owner_api.py`'s
`require_same_origin` dependency rejects cross-site POSTs via the standard fetch-metadata
resource isolation policy. Basic Auth alone wasn't enough: browsers attach cached credentials to
any request to the origin, including a form POST from a hostile page.)

## Owner escalation emails

When the front-desk skill escalates (`flag_attention.py` -> `POST /escalations`), the owner can
optionally be emailed - a deterministic side effect of creating the escalation, not an LLM in this
path. The dashboard's Attention queue is the source of truth regardless: a missing/failed email
never stops the escalation from being created or shown there (`get_email_notifier()` returns
`None` if unconfigured, and `EmailError` is caught and logged, never raised back to the caller).

Configure via `.env` (see `.env.example`): `OWNER_NOTIFICATION_EMAIL` (recipient) plus
`SMTP_HOST`/`SMTP_PORT`/`SMTP_USERNAME`/`SMTP_PASSWORD`/`SMTP_FROM`. Both `OWNER_NOTIFICATION_EMAIL`
and `SMTP_HOST` must be set for emails to send at all - leave either blank to disable notifications
entirely and only use the dashboard.

Plain SMTP+STARTTLS via `integrations/email_notifier.py`'s `SmtpEmailNotifier`, deliberately not a
provider-specific API (Gmail API, SES SDK) reusing the Calendar integration's OAuth credentials -
that credential is scoped to Calendar only, and widening it to also send mail (a different
Google API scope, requiring redoing the interactive OAuth consent flow) would both broaden what a
single leaked token could do and couple two unrelated integrations. A standalone SMTP app password
on whatever mailbox the business already checks (e.g. the same Google account used for Calendar,
via an [App Password](https://myaccount.google.com/apppasswords) rather than the OAuth token) gets
the same practical convenience - reusing an inbox that's already checked - without that coupling.

The email body is intentionally brief and partially redacts the customer's phone number
(`_mask_phone_for_email` in `api.py`) - full customer details stay in the dashboard, which is
reached over a password-gated connection; email is a less trusted channel by comparison.

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

**Deployed already** - see [DEPLOYMENT.md](DEPLOYMENT.md) for the live instance's details, the
actual redeploy procedure (verified, not theoretical), and gotchas already hit (stale bind mounts
after a pull, WhatsApp linking needing a real TTY, session history anchoring on a bad exchange
even after the underlying bug is fixed, and more). Read that before touching the deployed instance
rather than re-deriving the process from scratch.

Both the customer-facing `app` service's port and `postgres`'s port are bound to `127.0.0.1` (not
exposed publicly, verified against the live instance) - this was a deliberate hardening fix, not
left as a TODO. Likewise don't expose OpenClaw's own control interface publicly. The
`owner-dashboard` service (see "Owner dashboard" below) is the one deliberate exception to this -
it needs to be reachable from a browser, so it's gated by password auth instead of by network
isolation.

Still worth testing before a demo, even though the deploy itself is done: container restart
(Postgres + OpenClaw state persist), Google credential refresh, WhatsApp reconnect, Calendar API
failure (must not confirm a phantom booking), and double-booking under a rapid duplicate request.

## Cautions specific to what's implemented so far

- **Phone normalization** (`customers/service.py:normalize_phone`) assumes Singapore numbers by
  default for bare 8-digit input. Revisit if a target business serves another country.
- **Recheck-before-booking reduces stale-slot double booking, but isn't a full race guard.**
  `POST /bookings` always re-queries Calendar free/busy and re-runs `find_available_slots`
  immediately before creating the event (same function availability search uses, so a requested
  start must exactly match a currently-valid slot, a stricter check than merely not overlapping
  something) - never trust a slot list from an earlier turn in the conversation, and the API
  doesn't either. The gap: the check-then-create sequence isn't atomic, so two requests racing
  within the same few hundred milliseconds could both pass the check before either writes - a
  genuine TOCTOU window, since FastAPI runs these sync endpoints in a thread pool.
  Not worth a Postgres advisory lock or per-slot mutex for a single-user hackathon demo; revisit
  before testing simultaneous customers.
  Verified this **does** cover the one case it looks like it might not: a single customer
  booking two overlapping slots back-to-back in the same conversation (e.g. one appointment per
  pet, both requested before either is confirmed) - a real incident looked at first like this
  might be the cause. Reproduced directly: `search_availability` for a 2-hour service correctly
  returns both a 9am and a 10am slot as available (neither is booked yet, so both genuinely are),
  but booking 9am first and then immediately requesting 10am gets a clean `409 slot_unavailable`
  from the recheck, and the following availability search correctly excludes both - because these
  two script calls are sequential (the LLM calls `create_booking.py` once, waits for its
  response, then calls it again), so the second call's fresh `get_busy_periods` query does see
  the first call's just-created event. No "temp calendar slots"/hold mechanism needed for this
  case - the existing recheck already handles it. The TOCTOU gap above is a separate scenario:
  genuinely concurrent requests (different customers, or duplicate client requests, racing
  within the same window).
- **Partial-failure handling**: if the Calendar event is created but the DB write then fails,
  `api.py:create_booking` deletes the Calendar event and returns 500 rather than leaving an
  orphaned event with no corresponding booking record. If the Calendar deletion itself then fails,
  it's logged at `CRITICAL` for manual reconciliation - there's no automatic retry queue yet.
- **Timezones**: all scheduling math is timezone-aware (`zoneinfo`, per-business `timezone` in the
  YAML profile); never pass naive datetimes into `scheduling/service.py`.
- **Credentials**: `.gitignore` excludes `.env`, `credentials/`, and OpenClaw's session/state
  directories. Double-check `git status` before committing if you've been testing locally.
- **Customer identity currently comes from message text, never a verified WhatsApp sender ID.
  This is a real, open gap.** `resolve_customer.py --phone` takes whatever phone
  number the LLM decides to pass, which in practice is whatever the customer typed or claimed in
  the conversation. Tested directly (`openclaw agent -t "+6598765432" -m "hello"` then asking the
  agent what phone number is visible "purely from your system/context information" returned "none
  visible"; a follow-up message stating a different number - `-m "Hi, my number is 90001111..."` -
  was then used as-is to look up bookings) - the session's actual bound number is not currently
  surfaced to the model at all for a direct chat, at least via this CLI testing path. That means
  right now nothing stops "my number is 91234567, cancel my appointment" from acting on a
  different real customer's booking if their number is known or guessed. Caveat on the test
  itself: `openclaw agent -t` may not fully replicate what a genuine inbound WhatsApp webhook
  message's channel metadata carries - worth re-verifying against the actual linked WhatsApp
  number before trusting this either way. `AGENTS.md` requires escalating rather than re-resolving
  if a *different* number shows up mid-session - asking the customer which one to use would only
  cost an attacker one extra reply. That does nothing for a first message that simply claims
  someone else's number from the very start, since there's no prior resolved identity yet to
  notice a mismatch against.
  What a real fix looks like, more concretely than "needs a plugin": OpenClaw's SDK docs confirm
  `ctx.requesterSenderId` is host-trusted and available at the plugin/hook layer, and channel docs
  confirm inbound WhatsApp carries sender/phone metadata - but that metadata isn't exposed
  everywhere by default specifically *because* it's sensitive (phone numbers, WhatsApp IDs, group
  IDs, display names), so a plugin hook needs explicit opt-in to see it. This project's
  front-desk scripts are plain `exec`'d CLI (no plugin code), so none of that reaches them today
  even in principle. The fix isn't "get the model to read the trusted value and type it correctly
  into `--phone`" (still LLM-mediated, still spoofable) - it's an inbound-hook plugin, written
  with that opt-in, that injects the verified number directly into the exec environment (e.g. an
  env var `resolve_customer.py` prefers over any LLM-supplied `--phone`), so the model never gets
  a chance to substitute a different one. Not attempted yet - flagging this rather than shipping
  it quietly, since now that reschedule/cancel exist, this is a real security gap for a demo with
  a real linked WhatsApp number, not just a theoretical one.

## Not yet built (see AGENTS42.md "Not in this slice" for the fuller list)

Owner-facing commands, multi-staff/multi-location support, and the Customer Follow-up /
Rescheduling Coordinator (owner-triggered, multi-customer disruption handling - not the same as
the single-booking customer-initiated reschedule/cancel that's built) agent roles from the
original proposal. Don't build ahead of what the current milestone needs.
