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
own key for a different model) needs more than `models set`, which only switches between
providers already configured. The recipe below adds a custom provider and was verified live against the
hackathon gateway - **never put the raw API key in a repo file**; it goes in an env var scoped to
the gateway service only, referenced by name from the config.

1. Put the key in a systemd drop-in (Linux; adapt for launchd/Task Scheduler elsewhere) - replace
   `YOUR_KEY_HERE` and pick your own env var name:
   ```bash
   mkdir -p ~/.config/systemd/user/openclaw-gateway.service.d
   cat > ~/.config/systemd/user/openclaw-gateway.service.d/hackathon-gateway.conf <<'EOF'
   [Service]
   Environment=HACKATHON_GATEWAY_API_KEY=YOUR_KEY_HERE
   EOF
   systemctl --user daemon-reload && systemctl --user restart openclaw-gateway
   ```
2. Export the same variable in your own shell too (the `config patch` validation step below runs
   in your shell, not the service) - e.g. `export HACKATHON_GATEWAY_API_KEY=YOUR_KEY_HERE`.
3. Write a JSON5 patch (anywhere outside the repo, e.g. `/tmp/`) registering the env-based secrets
   provider and the model provider itself as a SecretRef, not a literal key:
   ```json5
   {
     secrets: { providers: { default: { source: "env", allowlist: ["HACKATHON_GATEWAY_API_KEY"] } } },
     models: {
       providers: {
         "hackathon-gateway": {
           baseUrl: "https://api.softwaresystems.app",
           apiKey: { source: "env", provider: "default", id: "HACKATHON_GATEWAY_API_KEY" },
           auth: "api-key",
           api: "ollama",  // matches the gateway's Ollama-compatible shape, per DEVELOPMENT.md
           models: [{ id: "global.anthropic.claude-sonnet-4-5-20250929-v1:0", name: "Hackathon Claude Sonnet 4.5", api: "ollama" }]
         }
       }
     }
   }
   ```
4. `openclaw config patch --file <path> --dry-run` first (fails loudly if the env var isn't
   resolvable), then re-run without `--dry-run` to apply. Confirm with `openclaw models list` -
   the new `hackathon-gateway/...` entry should appear. `openclaw config get models.providers` /
   `python3 -c "import json; print(json.load(open('~/.openclaw/openclaw.json'))...)"` never shows
   the raw key, only the SecretRef.
5. Test without touching the default: `openclaw agent --model hackathon-gateway/global.anthropic.claude-sonnet-4-5-20250929-v1:0 -m "hello"`.

**Switching to OpenRouter** (e.g. the hackathon gateway's rate limit is blocking you and you need
the AWS instance to keep answering WhatsApp messages in the meantime - this is what's actually
deployed there right now, see DEPLOYMENT.md): OpenRouter is a built-in provider, not a custom one
like the hackathon gateway above, so this is simpler - no JSON5 patch needed.

1. Add your OpenRouter API key (interactive prompt, paste a key like `sk-or-v1-...`):
   ```bash
   openclaw models auth login --provider openrouter --method api-key
   ```
   Stored as an OpenClaw auth profile (`openclaw models auth list` to confirm) - never touches
   this repo or any file you'd commit.
2. **Check which exact model ID actually exists before trusting one** - OpenRouter has several
   similarly-named variants of most models, and the one that sounds right may not be the one
   that's configured:
   ```bash
   openclaw models list --all --provider openrouter | grep -i deepseek
   ```
   The naming pattern is `openrouter/<provider>/<model>` - but check the exact suffix. As of this
   writing the AWS instance runs `openrouter/deepseek/deepseek-v4-flash-0731`, not the more
   generic-sounding `openrouter/deepseek/deepseek-v4-flash` (a real, different, separately-listed
   model) - verify with the command above rather than guessing from memory or this doc.
3. Test without changing the deployed default first:
   ```bash
   openclaw agent --model openrouter/deepseek/deepseek-v4-flash-0731 -m "hello" --json
   ```
   then a more useful check that the whole stack still works, not just the model connection:
   ```bash
   openclaw agent --model openrouter/deepseek/deepseek-v4-flash-0731 \
     --session-key "agent:main:openrouter-test" -m "what services do you offer?" --json
   ```
   Look for a real tool call (`toolSummary.calls >= 1`) and the actual business name in the
   reply - if that works, this instance's OpenClaw/backend setup is fine and the hackathon
   gateway really is the part currently blocked, not something else.
4. Once satisfied, make it the default the same way as any other configured model:
   ```bash
   openclaw models set openrouter/deepseek/deepseek-v4-flash-0731
   ```
5. **This is the model used for the actual submission** - originally a rate-limit workaround with
   a plan to switch back to the hackathon gateway before the demo, but as of 2026-09-23 the
   organiser still hasn't resolved the token/rate-limit issue despite repeated requests (a real
   `⚠️ API rate limit reached` was hit again that day, on a routine one-off test call). A hard
   rate limit mid-recording is a worse failure mode than a cheaper model, so this is now the
   deliberate choice for the submission - see DEPLOYMENT.md's "Model" entry. If
   the organiser actually fixes it with enough runway left to re-verify before recording, switch
   back with:
   ```bash
   openclaw models set hackathon-gateway/global.anthropic.claude-sonnet-4-5-20250929-v1:0
   ```

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
one). This is expected: the backend refuses to confirm a booking it can't verify. Confirm with
`docker compose logs app`.

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

**Backend returns 404 "No business profile found" (or similar) even though the file is right
there** - the running container's bind mount may be stale. This happens if the container was
started, then the working tree changed underneath it in a way that recreates directory inodes
(e.g. `git checkout`/`git pull` doing a fast-forward that introduces a directory for the first
time on this branch) - Docker's bind mount can end up pointing at the old, now-orphaned directory
instead of the current one. Confirm with a quick test (`echo test > businesses/_x && docker
compose exec app cat /app/businesses/_x`, then remove it) - if the container can't see a file you
just wrote, `docker compose up -d --force-recreate` fixes it by rebinding the mounts.

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
