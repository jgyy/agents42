# SME Solutions Shortlist & Action Plan (Pre-Kickoff)

Date: 2026-08-27
Status: Reference material — not a design commitment. Follows on from
`docs/2026-08-27-sme-problem-brainstorm.md`; fills in the Miro board's "Generate
solutions" and "Generate action plan" steps, which were still blank templates as of
that session.

## How This Was Scored

Each shortlisted solution is checked against the hackathon's stated judging themes
(from the design spec's Goals section): **practical & relevant**, **feasible to pilot**
(inside the ~3-week build phase), **secure & responsible**, **sound technical
architecture** (fit with the already-built reusable core), **effective use of agentic
AI**, and **measurable business impact**. Ratings are High / Medium / Low, judged
relative to each other, not on an absolute scale.

## Shortlisted Solutions

### 1. Rescheduling Agent — Appointment Changes & Rescheduling

Reads a WhatsApp/SMS change request, checks the booking system, proposes suitable
alternative slots, and executes policy-compliant rebookings directly — escalating to
staff (human-review interrupt) for exceptions (different doctor needed, no slot within
policy window, VIP/flagged patient).

Maps directly onto the existing core: extract (requested change) -> validate (policy
check + slot search) -> human-review on exception -> act (update booking) -> confirm.

| Theme | Rating | Why |
|---|---|---|
| Practical & relevant | High | Universal SME pain — works for any appointment-based business, not just clinics |
| Feasible to pilot | High | Closest structural match to the invoice pipeline already built |
| Secure & responsible | High | Human sign-off on anything outside policy is the same story as invoice anomalies |
| Sound architecture | High | Reuses Extractor/Validator/human-review shape with minimal new plumbing |
| Effective agentic AI | Medium | Decision-making is mostly rule/policy matching, less open-ended judgment |
| Measurable impact | Medium-High | Staff time saved and slot fill-rate are demoable |

### 2. Refill Coordinator Agent — Medication Refill & Collection

Checks the patient/prescription record, flags whether doctor approval is required,
routes to a mandatory human approval gate for anything medical, checks stock once
approved, requests payment, and tracks status through to collection.

Fits the core's shape but with more sequential stages than a single-shot record (a
multi-step state machine: approval -> payment -> prep -> collection) rather than one
extract/validate pass.

| Theme | Rating | Why |
|---|---|---|
| Practical & relevant | High | Concrete, frequent, multi-party coordination pain |
| Feasible to pilot | Medium | More sequential state to track than the invoice example; still buildable in 3 weeks |
| Secure & responsible | High | Mandatory human sign-off on medical decisions is the strongest fit for this judging theme |
| Sound architecture | Medium-High | Fits the core, but needs an extended multi-stage graph, not just new nodes |
| Effective agentic AI | High | Several sequential tool-using decisions (approval, stock, payment, notify) |
| Measurable impact | High | Staff time and reduced wasted customer trips are easy to quantify |

### 3. Stocktake Copilot Agent — Retail Sales Promoter Stocktaking

A promoter snaps a shelf photo; the agent identifies and counts products, compares
against expected/previous stock and recent sales, flags anomalies, recommends a
restock quantity, and — with promoter confirmation — logs the stocktake, notifies the
brand/distributor, and proactively suggests the next visit date.

Best showcase of the design spec's own claim that the Extractor's LLM call is
"swappable for a multimodal one without touching the graph shape" — extraction here is
vision-based instead of text-based, but the rest of the pipeline is unchanged.

| Theme | Rating | Why |
|---|---|---|
| Practical & relevant | High | Concrete, visual, easy to demo live |
| Feasible to pilot | Medium | Needs a multimodal extraction swap; vision accuracy on shelf photos is an untested risk |
| Secure & responsible | Medium | Lower stakes than medical, but still benefits from human confirm before committing a restock order |
| Sound architecture | High | Cleanest proof of the core's stated reusability claim (swap extraction modality only) |
| Effective agentic AI | High | Multi-source reasoning: image + historical stock + sales velocity |
| Measurable impact | High | Units restocked, stores prioritized, and sales uplift are all quantifiable |

### 4. Decision Support Agent (top pick) & Personal Possessions Agent (second pick) — Supporting People With Hoarding Behaviour

The board itself starred these two above the other two hoarding-support ideas
(Clutter Progress Agent, Digital Memory Agent), so they're the ones shortlisted here.

- **Decision Support Agent:** instead of instructing "keep/discard," asks guided
  questions per item/photo to help the person reach their own decision, and records
  the outcome.
- **Personal Possessions Agent:** identifies and logs possessions from photos, and
  answers queries like "do I already own a rice cooker?"

Neither fits the extraction-validation-export shape — both are stateful,
multi-session conversation/memory agents rather than a validator judging one record
per case. This is the concrete "doesn't fit at all" case the design spec's Open Risks
section anticipated.

| Theme | Rating | Why |
|---|---|---|
| Practical & relevant | High | Emotionally resonant, socially differentiated from pure admin-automation |
| Feasible to pilot | Low | Needs a new session/memory architecture the core doesn't have yet — not just a new schema/agents/prompts swap |
| Secure & responsible | Medium | Must stay clearly "support," not clinical/therapeutic — a real constraint to design around |
| Sound architecture | Low-Medium | Biggest structural departure of the four; Supervisor pattern still applies, but everything else is new |
| Effective agentic AI | High | Genuine judgment and guided dialogue, not rule-matching |
| Measurable impact | Low-Medium | Social impact is harder to quantify for hackathon judging than SME revenue/time metrics |

### Recommendation

If the team wants a mental "closest fallback" ranking while waiting for the real
problem at kickoff, in order of how directly they'd reuse what's already built:

1. **Medication Refill & Collection** — best combination of strong security/responsible
   story and measurable impact, for a moderate rise in build complexity.
2. **Appointment Rescheduling** — simplest, most direct reuse of the existing pipeline;
   the safest "if it looks like this, we're basically done" case.
3. **Retail Stocktaking** — highest payoff as a reusability showcase (multimodal swap),
   but carries real vision-accuracy risk worth a quick early spike if it comes up.
4. **Hoarding Support (Decision Support Agent)** — valuable and differentiated, but the
   biggest architecture departure; only worth treating as a real build target if the
   assigned problem is explicitly conversational/decision-support in shape, not a
   record-per-case workflow.

## Action Plan

### Phase 0 — Now through kickoff (27 Aug – 4 Sep 2026)

- Keep the invoice-processing example as the working proof-of-pattern; don't start
  building any of the four shortlisted problems for real — none of them may be the
  one actually assigned.
- Circulate this shortlist and the brainstorm catalog to the whole team so everyone
  can practice the "which pattern does this map to" classification before it matters.
- Chase the open question from the Miro board — *"Do you know an SME we can talk
  to?"* — for at least one contact in Vet/Dental/Clinic (rescheduling/refill) or
  retail (stocktaking), since two of the four shortlisted solutions cluster in that
  vertical; a real contact is useful validation even if the assigned problem differs.
- Confirm AWS Bedrock access/credits status ahead of kickoff (per the design spec's
  existing Open Risks note) so day one of the build phase isn't blocked switching off
  the `LLM_PROVIDER=anthropic` fallback.

### Phase 1 — Kickoff day (Sat 5 Sep 2026)

- Run the "What To Listen For At Kickoff" checklist from
  `docs/2026-08-27-sme-problem-brainstorm.md` against the real assigned problem within
  the first hour.
- Decide whether it maps to the extraction-validation-export shape (like three of the
  four shortlisted patterns) or the conversational/decision-support shape (like
  hoarding support) — this determines how much of the core is reused as-is versus
  needs new design work.
- Sketch the new `examples/<problem>/` schema/agents/graph/prompts shape the same
  afternoon, following the steps already in README.md's "Adapting this to the real
  SME problem" section.

### Phase 2 — Build phase (Mon 7 – Fri 25 Sep 2026, ~3 weeks)

- **Week 1 (7–11 Sep):** Stand up the new example's schema, prompts, and agent nodes
  on top of the untouched core (LLM provider, graph helpers, export tool). Get one
  clean end-to-end case working, the same way `invoice_processing` was proven out.
- **Week 2 (14–18 Sep):** Build out domain-specific decision logic and human-review
  triggers; add realistic sample data covering both clean and anomalous cases, mirroring
  the existing sample-invoice set; wire the Streamlit UI's trace view to the new domain.
- **Week 3 (21–25 Sep):** Harden edge cases, expand tests (deterministic rules stay
  unit-tested, LLM output stays mocked, per the existing Testing Strategy), and shape
  the demo narrative explicitly around the six judging themes.

### Phase 3 — Submission (by Mon 28 Sep 2026)

- Final green `pytest` run; update README.md for the real problem in place of the
  invoice-specific instructions; write the submission narrative referencing this
  brainstorm and shortlist as evidence of upfront reusability thinking.

### Phase 4 — Finale Demo Day (Sat 10 Oct 2026)

- Rehearse the live demo (CLI + Streamlit); prepare for architecture questions using
  the design spec and this shortlist as backing material; confirm the Anthropic
  fallback still works as a live-demo safety net if Bedrock has issues on the day.
