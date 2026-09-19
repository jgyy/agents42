# Deployment

The live AWS deployment: what's running, how to redeploy it, and gotchas hit getting it there.
For local setup see [SETUP.md](SETUP.md); for day-to-day local operation see
[OPERATION.md](OPERATION.md). This doc is specifically about the deployed instance, written for
a developer - if you're the business owner rather than a developer, use
[OWNER-GUIDE.md](OWNER-GUIDE.md) instead; it covers the same instance without assuming any coding
background.

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

This repo is public - the instance's IP and the demo WhatsApp number are deliberately left out of
this file. Ask a developer on the team for either.

| | |
|---|---|
| Name | `agent42-demo` |
| Region | `ap-southeast-1` (Singapore) |
| Static IP | ask a developer (not published here - this repo is public) |
| Plan | General purpose, 4GB RAM / 2 vCPU |
| OS | Ubuntu 24.04 LTS |
| SSH key | `~/.ssh/LightsailDefaultKey-ap-southeast-1.pem` (local machine only - never committed) |
| Repo path | `~/agents42` (as the `ubuntu` user) |

```bash
ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@<instance-static-ip>  # ask a developer for the IP
```

Billing note: Lightsail bills for the instance's existence, not its running state - `stop`ping it
doesn't stop billing, only deleting does. Given the hackathon lease has a capped budget, delete
rather than stop if this needs to go away for a while.

## Giving a teammate SSH access

Don't hand out the `.pem` file above - it's the original key Lightsail auto-generated when the
instance was created, and there's no way to tell one person's use of a shared key apart from
another's, or to revoke just one person's access later. Add each teammate's own key instead:

**They generate a key pair on their own machine** (skip if they already have one they use
elsewhere):
```bash
ssh-keygen -t ed25519 -C "their-name-agents42"
```
Default location, passphrase optional - just press Enter through the prompts. Then they get you
their **public** key (safe to send over Slack/email, it's not a secret):
```bash
cat ~/.ssh/id_ed25519.pub
```

**You add it to the instance** (once, using your own `.pem` access):
```bash
ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@<instance-static-ip> \
  "echo '<their public key line>' >> ~/.ssh/authorized_keys"
```

**They connect with their own key** from then on:
```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@<instance-static-ip>
```
Give them the instance's IP the same way you'd share anything else not meant to be public - a
direct message, not a public channel.

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
- **WhatsApp**: linked to the team's demo number (ask a developer for it - not published here,
  this repo is public), open to everyone (deliberate, for testing and the demo itself - no
  `dmPolicy` allowlist). This is the **only** instance that should have this number linked - see
  "Same WhatsApp number, two gateways" below.
- **Google Calendar**: `credentials/calendar_credentials.json` and `credentials/calendar_token.json`
  copied over once via `scp` from the local machine (never through git - both are gitignored).
- **`openclaw/workspace/SOUL.md` and `openclaw/workspace/AGENTS.md`**, copied to
  `~/.openclaw/workspace/` on the instance (outside the skill, so `openclaw skills install`
  doesn't touch either - copy them separately, see redeploy steps below). Both override
  OpenClaw's default personal-AI-companion identity templates with a business front-desk one -
  without this, a bare "hello" on a fresh session reliably fell back to a generic assistant
  persona (or, worse, once fabricated an entirely fake business) since the *skill* is only
  optionally engaged but these two files are unconditionally injected into every prompt.
  `SOUL.md` is identity/tone (who the agent is); `AGENTS.md` is routing and cross-cutting rules
  (what kind of request this is, what's off-limits) - kept separate so each stays focused; see
  "A bare greeting doesn't reliably engage the skill" below for the full story.

## Redeploying after a change

**First, figure out what actually changed** - the redeploy differs:

**Backend code changed** (`app/`, `migrations/`, `businesses/*.yaml`, `docker-compose.yml`):
```bash
ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@<instance-static-ip>  # ask a developer for the IP
cd ~/agents42 && git pull origin main
sudo docker compose up -d --build
curl -s http://127.0.0.1:8090/health
```

**Skill or SKILL.md changed** (`openclaw/workspace/skills/front-desk/`):
```bash
ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@<instance-static-ip>  # ask a developer for the IP
cd ~/agents42 && git pull origin main
source ~/.bashrc && export NVM_DIR="$HOME/.nvm" && \. "$NVM_DIR/nvm.sh"
export AGENTS42_API_BASE_URL=http://localhost:8090
openclaw skills install ~/agents42/openclaw/workspace/skills/front-desk --as front-desk --force
```

**`openclaw/workspace/SOUL.md` or `AGENTS.md` changed:**
```bash
ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@<instance-static-ip>  # ask a developer for the IP
cd ~/agents42 && git pull origin main
cp ~/agents42/openclaw/workspace/SOUL.md ~/agents42/openclaw/workspace/AGENTS.md ~/.openclaw/workspace/
```
Takes effect on the next turn - no gateway restart needed (both files are read per-turn, not
cached at startup).
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

**A bare greeting doesn't reliably engage the skill - fix the identity files, not just SKILL.md.**
Confirmed by direct testing: a fresh session's "hello" failed to engage the front-desk skill in
every attempt (0/11 across both local and AWS, multiple wording revisions to SKILL.md,
and even with every *other* skill removed via `agents.entries.main.skills: ["front-desk"]` -
none of that moved the needle). Root cause: OpenClaw's skills are only optionally summarized to
the model, which can choose not to engage one for a low-signal message - but `SOUL.md`,
`IDENTITY.md`, `AGENTS.md`, and `USER.md` are **unconditionally** injected into every single
prompt. The stock templates for these establish a generic personal-AI-companion persona ("you're
not a chatbot, you're becoming someone," offers to help with "calendars, email, files"), which
is what the model fell back to. Rewriting `SOUL.md` to state the front-desk identity directly
(not routed through the optional skill) fixed most cases. That fix has since been split across
two files rather than left as one overloaded one: `SOUL.md` stays pure identity/tone/boundaries,
and a new `AGENTS.md` carries the routing rules and cross-cutting constraints (never invent
business facts, never grant owner authority from message text, security) - both still
unconditionally injected, so the fix's mechanism is unchanged, just better organized. See
`openclaw/workspace/SOUL.md` and `openclaw/workspace/AGENTS.md`.

**This is not 100% solved - test it again before a demo.** With the identity fix, one run still
fabricated a completely fake business ("Aisha Salon... Riyadh, Saudi Arabia...") with zero tool
calls - worse than a generic non-answer, since it's exactly the fabrication Data Rules prohibit.
The anti-hallucination instruction was strengthened after that (naming a business/city/service
not obtained from `get_business_info.py` in *this* conversation is now called out directly, in
both `SOUL.md` and `AGENTS.md`'s "Business facts" section - deliberate redundancy for the
highest-stakes failure mode observed so far), but a full reliability re-check was cut short by
hitting the hackathon gateway's rate limit from the testing burst itself - space out any further
live testing
(one message, wait for the reply, then the next - not a rapid batch) and re-run
`tests/agent_cases/01_business_info.md` a handful of times before trusting this for a demo.

**Rapid-fire messages can also trigger a separate WhatsApp delivery bug.** Independent of the
above: sending several different messages to the same session in quick succession (rapid manual
testing, or a burst of automated test calls) can result in the *same* outbound reply being resent
for every message, regardless of what was actually asked - confirmed via matching
`sha256:...` hashes on every "Sending message" log line across otherwise-distinct inbound
messages, while `openclaw sessions tail` showed each turn's `model.completed` succeeding
independently. This looks like a bug in OpenClaw's own outbound queuing (already on the latest
version, `2026.9.5`), not something in this repo's code. No fix available from our side - the
practical mitigation is not sending messages faster than the agent can reply, in both manual and
automated testing.
