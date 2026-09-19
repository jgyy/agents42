# Operation Guide

Running and troubleshooting an already-set-up agents42 stack day to day. If you haven't set it up
on this machine yet, see [SETUP.md](SETUP.md) first.

## Starting everything

```bash
docker compose up -d          # backend: FastAPI + Postgres
curl http://localhost:8090/health
```

OpenClaw normally runs as a background service already (systemd on Linux) and doesn't need
starting manually. Check with:
```bash
systemctl --user status openclaw-gateway   # Linux
openclaw status
```

## Stopping everything

```bash
docker compose stop           # keeps data; `down` also removes the containers
systemctl --user stop openclaw-gateway     # only if you want WhatsApp offline too
```

Don't run `docker compose down -v` unless you want to drop all local Postgres data (customers,
bookings) - see "Resetting test data" below for a more targeted option.

## Checking status

```bash
docker compose ps
curl http://localhost:8090/health
openclaw status                # gateway, channels (WhatsApp), sessions, model provider
openclaw channels list         # WhatsApp linked/not
```

## Switching LLM providers/models

Per DEVELOPMENT.md's three-mode table: cheap model for daily dev, the hackathon gateway for
early compatibility checks and final integration. These commands do the actual switching
(verified live against this setup):

```bash
openclaw models list                              # what's configured, which is default
openclaw models set openrouter/anthropic/claude-sonnet-4.6   # change the persistent default
```

`models set` writes `~/.openclaw/openclaw.json` (with a `.bak` backup made automatically) - this
affects every future conversation, not just your next message. For a one-off test without
changing the default:

```bash
openclaw agent --model openrouter/anthropic/claude-sonnet-4.6 -m "test message"
```

**Adding a new provider you haven't configured yet** (e.g. the hackathon's AWS gateway, or your
own key for a different model) isn't a `models set` - that only switches between providers
already configured. Add it via `openclaw onboard` again (same command as initial setup, safe to
re-run) or `openclaw config set` for a specific non-interactive value - see SETUP.md step 4 and
`openclaw config set --help`.

## Testing without messaging real WhatsApp

```bash
openclaw agent -m "Can I book grooming this Friday afternoon?"
```

Runs one turn through the real gateway/skill/backend and prints the reply, without needing a
phone. Add `--session-key "agent:main:<some-name>"` to keep a test conversation isolated from
your real WhatsApp session history, and `--json` for the full machine-readable trace (tool calls,
token usage, cost) if you're debugging *why* it did something.

You can also hit the backend directly, bypassing the LLM entirely, to isolate whether a problem is
in the agent's reasoning or in the backend/Calendar:
```bash
curl -X POST http://localhost:8090/customers/resolve -H 'Content-Type: application/json' \
  -d '{"phone":"91234567","name":"Test User"}'
curl -X POST http://localhost:8090/availability/search -H 'Content-Type: application/json' \
  -d '{"business_id":"demo-groomer","service":"full_grooming","date":"2026-09-25"}'
```

## Logs

```bash
docker compose logs -f app          # backend: bookings, Calendar rollback events, errors
docker compose logs -f postgres
openclaw logs                        # gateway logs (tail via RPC)
```

Booking failures and the orphaned-Calendar-event rollback (see DEVELOPMENT.md "Cautions") log at
`ERROR`/`CRITICAL` in the backend's logs - grep for `agents42.api` if the stream is noisy.

## Safety: who can message the agent

By default a newly-linked WhatsApp number accepts messages from anyone. Before a real demo or
whenever you're not actively watching it, restrict this so a stranger can't trigger a real booking
on whatever calendar is connected:
```bash
openclaw channels list       # find the whatsapp account/channel id
```
Then set a `dmPolicy` allowlist for that channel (see `openclaw channels --help` /
`openclaw config --help` for the exact non-interactive form, or use `openclaw onboard` again to
walk through it) - add your own number and any teammates' numbers who need to test it.

## Resetting test data

There's no cancellation/delete endpoint yet (this slice deliberately doesn't build it - see
AGENTS42.md "Not in this slice"), so cleaning up one test booking is two manual steps:

**1. Delete the Calendar event** - find it on the calendar (or via `google_event_id` in the
booking response / `GET /bookings/<id>`) and delete it from Google Calendar directly.

**2. Mark the DB row cancelled**, so it stops being treated as a real booking (e.g. by future
availability checks against the DB, if that's ever added) - connect to Postgres directly:
```bash
docker compose exec postgres psql -U agents42 -d agents42 \
  -c "UPDATE bookings SET status = 'cancelled' WHERE id = '<booking-id>';"
```
Leaving a stray test row with `status = 'confirmed'` isn't harmful today (only Google Calendar's
own free/busy is checked for availability, not this table), but keeping it tidy avoids confusion
if you're eyeballing the database.

**Wipe everything and start clean:**
```bash
docker compose down -v        # drops customers/bookings, keeps your .env and credentials/
docker compose up -d --build
```
This does **not** touch your Google Calendar - delete test events there separately if you want
them gone too.

## Common problems

**`Error 403: access_denied` during Google OAuth** - your Google account isn't on the OAuth
consent screen's test-user list yet. Ask whoever owns the Google Cloud project to add you (Cloud
Console -> APIs & Services -> OAuth consent screen -> Test users), then retry.

**Backend returns 502 "Calendar unavailable"** - either `credentials/calendar_token.json` doesn't
exist yet (run the OAuth flow, SETUP.md step 3) or the refresh token was revoked (Google account
security settings -> Third-party access -> revoke, then just re-run the OAuth flow to get a fresh
one). This is working as intended, not a bug - the backend refuses to confirm a booking it can't
verify. Confirm with `docker compose logs app`.

**Agent gives a generic/escalation reply instead of using the skill** - check
`openclaw skills list` shows `front-desk` as "✓ ready". If you edited `SKILL.md` or the scripts,
reinstall it (`openclaw skills install ./openclaw/workspace/skills/front-desk --as front-desk
--force`) - it doesn't hot-reload.

**Scripts can't reach the backend / connection refused** - `AGENTS42_API_BASE_URL` isn't set where
the gateway process actually runs. An `export` in your shell only affects commands run in that
shell, not the background systemd service - see SETUP.md step 4's systemd drop-in, and remember to
`systemctl --user daemon-reload && systemctl --user restart openclaw-gateway` after changing it.

**Port already in use** (Docker fails to bind, or `curl` connects to the wrong thing) - something
else on this machine already owns that port (Portainer on 8000 is a common one). Change the host
side of the port mapping in `docker-compose.yml`, and update `AGENTS42_API_BASE_URL` in both
`.env` and the systemd drop-in to match, then restart both the backend and the gateway.

**Double-booking / "slot is no longer available" on a slot you just saw offered** - this is the
recheck-before-booking guard working correctly (DEVELOPMENT.md "Cautions"), most often seen when
two people (or two test sessions) grab the same slot near-simultaneously. Re-search for fresh
slots rather than retrying the same time.

## Multiple people testing at once

Each person running their own full stack (their own Docker backend, their own OpenClaw, their own
WhatsApp number) is the simplest setup and matches SETUP.md - no shared state, no coordination
needed, and everyone's test bookings land on whichever calendar *they* connected. Only coordinate
if you deliberately want a shared team calendar (SETUP.md step 3's optional section) or a single
shared WhatsApp number for a joint demo - in either case, agree on who's actively testing before
you send messages, since a stranger's message and your teammate's message look identical to the
agent.
