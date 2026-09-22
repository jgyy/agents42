# Design Decision: Owner Dashboard Before Owner Agent

**Date:** 2026-09-20
**Status:** Decided and implemented (see `feat/owner-dashboard`)

## Context

By this point the customer journey was implemented end-to-end: enquiries, booking, checking
bookings, rescheduling, and cancellation (see AGENTS42.md). The original proposal's five agent
roles include an **Owner Assistant Agent** and a **Rescheduling Coordinator** (an owner-triggered,
multi-customer disruption workflow — "staff unavailable tomorrow → find affected bookings →
propose alternatives → owner approves → notify customers"). The natural next question was which
of these to build next.

## The decision

Build a small **operational dashboard** for the business owner first, and treat a full owner AI
agent as a stretch goal on top of the same backend — not the other way around.

## Why

A full owner agent is close to another major product slice, not an incremental addition to the
one that exists. It needs its own conversational interface, a different trust level than a
customer ever gets, more powerful tools, and actions that can affect multiple customers at once
(the disruption-coordination scenario above). That's a meaningfully larger surface for edge cases,
and a real risk of running out of time before the demo with neither piece fully working.

A dashboard gets most of the practical value sooner, with far less risk:

- **The grading matrix isn't only "how many agents."** It's checked against six themes — practical
  & relevant, feasible to pilot, secure & responsible, sound technical architecture, effective
  agentic AI, and measurable business impact (`docs/2026-08-27-sme-solutions-and-action-plan.md`).
  A useful operational interface strengthens several of those directly, without weakening the
  agentic story — the customer-facing agent already carries that, and the disruption-coordination
  workflow stays available as a later, more impressive agentic capstone.
- **It gives the team (and the owner) one place to see the whole system.** Before this, checking
  on the system meant three separate tools — Postgres, Google Calendar, and OpenClaw's own logs.
  The dashboard is a single visual source of truth for what the agent has actually done.
- **It forces a real human-escalation path to exist, on its own merits.** Before the dashboard,
  when the agent said "the business will follow up," that was purely conversational text with
  nowhere to land. Building the dashboard's Attention queue meant building `POST /escalations`
  and `flag_attention.py` first — infrastructure a future owner agent would need anyway, now
  proven out independently.
- **It's modular, demoable progress.** Each piece (bookings view, manual reschedule/cancel,
  blocking time, customer records, business profile visibility, escalation queue) is independently
  useful and independently shippable, rather than one large feature that's all-or-nothing by demo
  day.
- **The backend work is not thrown away if the owner agent gets built.** The dashboard's actions
  (reschedule, cancel, block time) call the same core booking logic the customer agent already
  uses; an owner agent later becomes a third interface over that same API, not a parallel system
  with its own plumbing to build from scratch.

## Architecture

```text
                    Backend / Owner API
                   /                  \
        Customer Agent          Owner Dashboard (built)
                                  Owner Agent (later, stretch)
```

The dashboard and a future owner agent are **parallel interfaces over the same backend**, not a
chain where one leads to the other. This is the same reasoning the codebase already applies to
keeping Customer Service and Scheduling in one skill rather than splitting them prematurely (see
AGENTS42.md "Agent roles: proposal vs. this slice") — build the shared foundation once, add
interfaces on top of it as they're needed, not before.

## What this means for the demo story

Even if the owner agent doesn't get built in time, there is still a complete, coherent system to
demo: customer-facing AI front desk + deterministic booking system + human escalation + an owner
dashboard that makes the whole thing visible and controllable. That's a stronger fallback position
than a half-finished second agent would be, and if there's time left over, the owner agent becomes
a bonus on top of a system that's already fully working end to end.

## What was actually built

First version live on `feat/owner-dashboard` (not yet merged as of this writing): today's/upcoming
bookings with manual reschedule/cancel, blocking off unavailable time, checking availability, a
searchable customer directory with per-customer booking history, a read-only view of the business
profile the agent itself reads facts from, and an escalation/"Attention" queue fed by the
front-desk skill's `flag_attention.py`. Single shared-password auth, one server-rendered page, no
frontend framework — kept deliberately small. See DEVELOPMENT.md "Owner dashboard" for how to run
it and its documented limitations, and AGENTS42.md "Owner dashboard (not the Owner Assistant Agent
role)" for how it relates to the still-unbuilt agent role.
