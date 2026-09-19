# Deployment

The live AWS deployment: what's running, how to redeploy it, and gotchas hit getting it there.
For local setup see [SETUP.md](SETUP.md); for day-to-day local operation see
[OPERATION.md](OPERATION.md). This doc is specifically about the deployed instance.

## The two copies of this code

The code exists in two places, and they only stay in sync when you explicitly sync them:

1. **GitHub** (`main`) - the source of truth. Every change goes here first, via a PR.
2. **The Lightsail instance** - a deployment target. It has its own clone of the repo, checked
   out to whatever commit it was last pulled to. It does **not** auto-update - pushing to GitHub
   changes nothing on the running instance until someone SSHes in and pulls.

**Workflow for any change:** commit -> push -> PR -> merge to `main` -> SSH into Lightsail ->
`git pull` -> redeploy (see below). There is no step that skips GitHub - even a one-line fix
should go through a PR and get pulled, not be edited directly on the server. (We broke this rule
once already, scp'ing a fix directly to the server to unblock live debugging - it worked, but it
meant the server and GitHub briefly disagreed until the PR caught up and `git pull` reconciled
them. Treat that as the exception you fall back to only when actively debugging live, not the
normal path.)

## Current instance

| | |
|---|---|
| Name | `agent42-demo` |
| Region | `ap-southeast-1` (Singapore) |
| Static IP | `47.130.223.152` |
| Plan | General purpose, 4GB RAM / 2 vCPU |
| OS | Ubuntu 24.04 LTS |
| SSH key | `~/.ssh/LightsailDefaultKey-ap-southeast-1.pem` (local machine only - never committed) |
| Repo path | `~/agents42` (as the `ubuntu` user) |

```bash
ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@47.130.223.152
```

Billing note: Lightsail bills for the instance's existence, not its running state - `stop`ping it
doesn't stop billing, only deleting does. Given the hackathon lease has a capped budget, delete
rather than stop if this needs to go away for a while.

## What's running there

- **Docker Compose** (FastAPI + Postgres) - same `docker-compose.yml` as local, ports bound to
  `127.0.0.1` only (verified unreachable from the public internet). Real random Postgres password
  generated on first deploy, not the `change-me` placeholder.
- **OpenClaw**, installed natively (not in Docker - see DEVELOPMENT.md), running as a systemd user
  service (`openclaw-gateway`) with lingering enabled so it survives SSH logout and reboots.
- **`front-desk` skill**, installed from the repo, pointed at the local backend
  (`AGENTS42_API_BASE_URL=http://localhost:8090`, set via a systemd drop-in - see below).
- **Model**: the hackathon's AWS Bedrock gateway (`api.softwaresystems.app`), set as the
  **default** model on this instance specifically - unlike local dev boxes, which should stay on
  a cheap model. Registered as a custom OpenClaw provider; the API key lives in a systemd-scoped
  env var, never in `openclaw.json` or git - see OPERATION.md "Switching LLM providers/models" for
  the exact (reusable) recipe.
- **WhatsApp**: linked to `+65 8141 4315`, open to everyone (deliberate, for testing and the demo
  itself - no `dmPolicy` allowlist). This is the **only** instance that should have this number
  linked - see "One WhatsApp number, one gateway" below.
- **Google Calendar**: `credentials/calendar_credentials.json` and `credentials/calendar_token.json`
  copied over once via `scp` from the local machine (never through git - both are gitignored).

## Redeploying after a change

**First, figure out what actually changed** - the redeploy differs:

**Backend code changed** (`app/`, `migrations/`, `businesses/*.yaml`, `docker-compose.yml`):
```bash
ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@47.130.223.152
cd ~/agents42 && git pull origin main
sudo docker compose up -d --build
curl -s http://127.0.0.1:8090/health
```

**Skill or SKILL.md changed** (`openclaw/workspace/skills/front-desk/`):
```bash
ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@47.130.223.152
cd ~/agents42 && git pull origin main
source ~/.bashrc && export NVM_DIR="$HOME/.nvm" && \. "$NVM_DIR/nvm.sh"
export AGENTS42_API_BASE_URL=http://localhost:8090
openclaw skills install ~/agents42/openclaw/workspace/skills/front-desk --as front-desk --force
```
No Docker rebuild needed - the skill isn't containerized.

**Docs only** (`.md` files): just `git pull`, nothing to redeploy.

**If `git pull` refuses because of local changes on the server** - this means something was
edited directly on the server (see the scp exception above) and hasn't caught up with a merged
PR yet. Diff it first (`git diff <file>`); if it matches what's incoming, `git checkout --
<file>` and pull again. Never blindly discard a server-side diff without checking what it is.

## Gotchas hit deploying this (read before you hit them again)

**Stale bind mounts after `git pull`.** If the Docker containers were already running and a `git
pull`/`checkout` recreates a directory's inodes (e.g. a directory that's new on the branch being
merged in), the running container's bind mount can end up pointing at the old, orphaned directory
- symptoms look like a 404 on data that's clearly right there in the repo. Fix: `docker compose up
-d --force-recreate`. Full explanation in OPERATION.md "Common problems."

**`openclaw onboard` / `openclaw channels add` need a real TTY.** Piped SSH commands (`ssh host
'command'`) can't provide one - these fail with "Interactive channel setup requires a TTY" no
matter what flags you throw at them (`ssh -t` doesn't help over a non-interactive connection
either). For WhatsApp linking specifically, `openclaw daemon install` (installing the gateway
service itself) *is* non-interactive and works fine piped - it's specifically the guided
wizard/QR-linking flows that need a live terminal. Open a real interactive SSH session for those.

**Real WhatsApp sessions can get "poisoned" by a bad exchange, even after the underlying bug is
fixed.** A real WhatsApp conversation persists as session `agent:main:main` (shared across
messages from that number). If the agent gives a wrong answer once, later turns can anchor on its
own earlier statement even with corrected instructions deployed. `agent:main:main` can't be
deleted (`sessions delete` refuses it - it's the protected default), but it can be reset with
`openclaw sessions compact "agent:main:main" --max-lines 1`. Test the fix against a **fresh**
session key first (cheap, isolates "is the fix right" from "is history poisoning it"), then
compact `agent:main:main` before declaring it fixed for real users.

**A model can quietly deviate from the documented tool-call procedure.** The front-desk skill
hit exactly this: on a real (not CLI-test) session, the model tried `ls`/`read` to hunt for
business data on disk instead of running `get_business_info.py`, guessing a path that never
existed. SKILL.md now explicitly forbids filesystem exploration as a fallback (see the fix
commit) - if you add a new script, add an equally explicit "don't do X instead" line, don't
assume the model will infer it from "use the script" alone.

**Same WhatsApp number, two gateways = duplicate/conflicting replies.** WhatsApp allows multiple
simultaneous linked devices per account. If both a local dev machine and a deployed instance are
linked to the same number, both receive and answer every inbound message independently -
confusing and hard to debug (a reply might come from either one, using different backends/models).
Only one gateway should ever be linked to the number people are actually messaging. When we moved
to Lightsail, we unlinked the local machine's WhatsApp session (`openclaw channels logout
--channel whatsapp`) so AWS is the sole responder.
