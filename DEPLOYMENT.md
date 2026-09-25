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

Every teammate should end up with their own key, not a copy of the `.pem` above (Lightsail's
original, auto-generated when the instance was created) - a shared key means no way to tell one
person's session from another's, or to revoke just one person's access later. There's no
password auth on this box, so *someone* has to already have access to authorize a new key -
pick whichever of these two fits:

**They generate a key pair on their own machine either way** (skip if they already have one they
use elsewhere):
```bash
ssh-keygen -t ed25519 -C "their-name-agents42"
```
Default location, passphrase optional - just press Enter through the prompts.

### Option A - you add their key for them

They send you their **public** key (safe over Slack/email, it's not a secret):
```bash
cat ~/.ssh/id_ed25519.pub
```
You append it, using your own `.pem` access:
```bash
ssh -i ~/.ssh/LightsailDefaultKey-ap-southeast-1.pem ubuntu@<instance-static-ip> \
  "echo '<their public key line>' >> ~/.ssh/authorized_keys"
```
They connect with their own key from then on:
```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@<instance-static-ip>
```

### Option B - share the `.pem` once as a bootstrap, they self-serve

Reasonable shortcut for a team of developers on a short timeline. Send them the `.pem` directly
(same "private channel only" rule as always). They log in with it once and add their own key
themselves:
```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub -o "IdentityFile=~/.ssh/LightsailDefaultKey-ap-southeast-1.pem" ubuntu@<instance-static-ip>
```
(or manually append their public key to `~/.ssh/authorized_keys`, same as Option A's command,
just run by them instead of you). From then on they use their own key, same as Option A. Only
real downside: they've now held a copy of your private key, even briefly - that's not undoable
after the fact (deleting their copy doesn't erase that it existed). If strict "nobody but me ever
had this key" matters to you, use Option A instead.

Either way, give them the instance's IP the same way you'd share anything else not meant to be
public - a direct message, not a public channel.

## What's running there

- **Docker Compose** (FastAPI + Postgres + owner dashboard) - same `docker-compose.yml` as local.
  The customer-facing `app` and `postgres` ports are bound to `127.0.0.1` only (verified
  unreachable from the public internet). Real random Postgres password generated on first deploy,
  replacing the `change-me` placeholder. `owner-dashboard` is the one deliberate exception - see below.
- **Owner dashboard**, port 8091, gated by `OWNER_DASHBOARD_PASSWORD` (HTTP Basic Auth). Two ways
  to actually reach it - see DEVELOPMENT.md "Owner dashboard" for the full comparison:
  1. **Direct HTTP** - requires a manual Lightsail firewall rule (not done via SSH): instance page
     -> Networking tab -> IPv4 Firewall -> Add rule -> Custom TCP, port 8091 (restrict the source
     to the owner's known IP if they have a stable one; otherwise this is plain HTTP open to
     whoever finds the port).
  2. **SSH tunnel** (`ssh -L 8091:localhost:8091 ...`) - no firewall rule needed, traffic stays
     inside the already-open SSH connection instead of a new public port. Preferred for a
     developer/teammate checking the dashboard; direct HTTP is more about the owner themselves
     being able to just open a URL without needing SSH access at all.
  Ask a developer for the dashboard URL/IP and password rather than guessing - not published here,
  this repo is public.
- **Owner escalation emails** (optional) - `OWNER_NOTIFICATION_EMAIL` + `SMTP_*` in `.env`, same
  file as the dashboard password. Unset means escalations still work, just aren't emailed - see
  DEVELOPMENT.md "Owner escalation emails".
- **OpenClaw**, installed natively (not in Docker - see DEVELOPMENT.md), running as a systemd user
  service (`openclaw-gateway`) with lingering enabled so it survives SSH logout and reboots.
- **`front-desk` skill**, installed from the repo, pointed at the local backend
  (`AGENTS42_API_BASE_URL=http://localhost:8090`, set via a systemd drop-in - see below).
- **Model**: running `openrouter/deepseek/deepseek-v4-flash-0731`, registered as a custom OpenClaw
  provider with the API key in a systemd-scoped env var, never in `openclaw.json` or git.
  **Decided, not a stopgap**: this was originally swapped in as a workaround for the hackathon's
  AWS Bedrock gateway (`hackathon-gateway/global.anthropic.claude-sonnet-4-5-20250929-v1:0`)
  rate-limiting testing, with the plan to switch back before the demo/submission. As of
  2026-09-23, the organiser still hasn't resolved the token/rate-limit issue despite repeated
  requests, and a hard rate limit mid-demo (confirmed still happening - see the `⚠️ API rate
  limit reached` error hit during a routine SSH-tunnel test that day) is a far worse failure mode
  for a recorded video than a cheaper model. **Staying on OpenRouter/deepseek for the actual
  submission** - re-evaluate only if the organiser actually fixes the hackathon gateway with
  enough runway left to re-verify before recording. See OPERATION.md "Switching LLM
  providers/models" for the recipe either way, and check `openclaw models list | grep default` on
  the instance for the actual ground truth rather than trusting this doc, since it can drift.
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
curl -s http://127.0.0.1:8091/health   # owner-dashboard - no Basic Auth needed for /health
```
No service name on `up -d --build` is deliberate - `app` and `owner-dashboard` share one built
image (see `docker-compose.yml`), so this rebuilds/recreates both together in one step.

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

**A model can quietly deviate from the documented tool-call procedure - and denying it one
wrong tool doesn't mean it stops wandering, it may just wander somewhere else.** The front-desk
skill hit this twice. First: on a real (not CLI-test) session, the model tried `ls`/`read` to
hunt for business data on disk instead of running `get_business_info.py`, guessing a path that
never existed. SKILL.md forbade filesystem exploration as a fallback - that fixed *that* symptom.
Second, found 2026-09-25 while collecting evaluation evidence for the submission video: a bare
"hello" was still occasionally taking 170-220 seconds. Traced turn-by-turn with `openclaw
sessions tail`: the model was still calling `ls`/`read` before ever calling `exec`, despite the
existing prohibition already living in two unconditionally-loaded files (SKILL.md and AGENTS.md).
Denying `ls`/`read` at the OpenClaw config level (`agents.entries.main.tools.deny`) stopped those
two calls specifically - but the model just pivoted to *other* irrelevant tools instead
(`gateway`, `conversations_list`, `sessions_list`), so a "hello" still took 217 seconds once.
Narrowing the whole tool profile (`tools.profile: "messaging"`, ~58 tools down to 17) fixed speed
dramatically (20s) - but in one test the model tried `sessions_spawn`/`sessions_yield` instead of
`exec` and the reply came back **completely empty**, which is strictly worse than slow. Reverted
that change rather than risk it landing during recording. The fix that actually shipped
(`fix/greeting-tool-exploration-reliability`) is prompt-only: broadened the filesystem-exploration
rule into a general "`exec` is the only tool this skill ever needs, for any reason" rule naming
the specific tools observed in the wild, and made `get_business_info.py` explicitly the required
*first action* for a greeting, before anything else. Verified: the 5-run greeting-reliability
sweep went from 4/5 (one 120s timeout) to 5/5 after deploying it. Two lessons here. First: a
documented instruction living in an always-loaded file can still go unfollowed - naming the
specific wrong tools mattered more than restating the rule abstractly. Second: speed and safety
are separate axes - the tool-profile change was faster *and* broke a real case; reversibility and
testing the actual failure mode mattered more than the speed win.
If you add a new script, add an equally explicit "don't do X instead" line, don't assume the
model will infer it from "use the script" alone - and don't assume denying one specific wrong
behavior means the model won't find a different wrong one to replace it with.

**A test can fail its own precondition without the underlying system being wrong.**
`tests/agent_cases/09_identity_switch.md` started failing on 2026-09-25 once the greeting-timeout
bug above stopped masking it - the timeouts had been hiding a real gap: turn 1 completed but
simply never escalated. Investigated rather than assumed: turn 0 asked about an appointment using a
brand-new phone number, and per SKILL.md's own documented "Manage an Existing Booking" step 1, a
phone with `needs_name: true` never gets a customer record created - the flow just says "no
booking on file" and stops. Confirmed directly against the database: no customer row existed
after turn 0. That means the escalation rule's actual precondition ("once a customer is resolved
in this session") never triggered, so turn 1 wasn't a switch away from anything - the test's own
scenario never set up what it claimed to test. Verified the real system behavior by replaying the
identical two-turn attack against a genuinely pre-existing customer with a real booking (seeded
and cleaned up by hand, not through the test harness): refused the switch, escalated to the
owner, left the real booking untouched - exactly the documented, intended behavior. Fixed the
test itself (`fix/identity-switch-test-precondition`) so turn 0 states a name and clear booking
intent, which actually creates the customer (verified against the database) before turn 1 runs.
The guardrail was correct the whole time - the test asserting otherwise wasn't actually
exercising it. Don't trust a test's own docstring about what it covers - check what it actually
triggers.

**These two findings only exist because the design keeps the LLM's job small.** Both incidents
above are about the model wandering when the plan is ambiguous. A booking, price, or availability
fact coming out wrong is a structurally different, more severe class of error that can't happen
here, since those facts never come from the model in the first place (see AGENTS42.md's
Guardrails). The model's only real job is deciding *which* narrow, typed script to call and
relaying its result - smaller surface area for exactly this kind of failure than a system that
trusted the LLM with more. Worth remembering when reading either finding above: **the specific
failure mode is a property of the exact model in use** (`openrouter/deepseek/deepseek-v4-flash-0731`
for both incidents - see this doc's "Model" entry), separate from the architecture itself. A different
model might wander less, or wander differently; the deny-list/profile/prompt fixes here were
tuned against what this specific model actually did, verified by tracing it, not assumed from
first principles.

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
(one message at a time, waiting for each reply, never a rapid batch) and re-run
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
