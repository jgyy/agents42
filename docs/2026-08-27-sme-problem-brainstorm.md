# SME Problem Brainstorm (Pre-Kickoff)

Date: 2026-08-27
Status: Reference material — not a design commitment. Source: team Miro board,
https://miro.com/app/board/uXjVHtp9oPw=/

## Purpose

The real SME problem statement for the hackathon isn't assigned until the kickoff on
5 Sep 2026 (see the design spec's Context section). Ahead of that, the team ran a
brainstorm across many SME verticals to surface what kinds of repetitive, frustrating,
or time-consuming work an agent could plausibly take on — the general shape being
"an agent can find information, make decisions, use tools, and take actions," the test
being "what makes someone say: *I wish somebody would just handle this for me*."

This doc distills that session into a reusable catalog: the SME landscape considered,
the recurring problem categories, four problems worked through in depth, and how each
one maps onto the reusable core described in
`docs/superpowers/specs/2026-08-15-agents42-starter-kit-design.md`. Use it to quickly
classify whichever real problem is assigned at kickoff — not as a substitute for it.

## SME Landscape Considered

| Area | Examples |
|---|---|
| F&B / Retail | Restaurant/cafe, hawker, retail shops, vending machines, e-commerce |
| Services | Contractors, hairdressing, legal/accounting, distributors, consultancy, home-based business, funeral services (pets and humans), logistics/delivery, spectacles/opticians, renovation & fixtures (aircon), events/parties, insurance, cleaning services, repair |
| Education | Tuition/enrichment, training providers |
| Healthcare | Vets, clinics/dental |
| Others | Non-profit, social/community organisations |

("Operations" and "Management" showed up on the board as cross-cutting problem
categories rather than SME types — see the taxonomy below.)

## Problem Taxonomy ("What Sucks")

Six recurring problem categories surfaced across these SMEs:

| Category | Typical pain points |
|---|---|
| Customer | Answering enquiries, complaints, bookings, follow-ups, ordering/recommendations, tracking customers/clients |
| Admin | Forms, documents, email/WhatsApp/phone calls, scheduling, data entry, reports, summarising information |
| Money | Quotations, invoices, payments, expenses, purchasing, receipts |
| Operations | Task tracking, suppliers, deliveries, inventory, staffing |
| Management | Checking KPIs, finding problems, deciding what needs attention |
| Marketing | Social engagement, ads, social presence |

Worked example from the session: *"Staff spend a lot of time answering WhatsApp
messages and manually finding booking slots."*

## Four Problems Explored in Depth

Each follows the structure used on the board: who has it, what they're trying to do,
how it's done today, what makes it painful, how often it happens, tools involved,
information needed, decisions required, actions taken, the cost of doing nothing, and
the agent opportunity.

### 1. Appointment Changes & Rescheduling (Vet / Dental / Clinic)

- **Problem:** Patients/customers book appointments in advance, but changes often come
  in through SMS/WhatsApp. Staff manually check the appointment, respond, and rearrange
  the booking.
- **Who:** Reception/admin staff, doctors/vets/dentists, patients/customers.
- **Goal:** Change, cancel, or reschedule an existing appointment.
- **Current process:** Customer sends SMS/WhatsApp -> staff reads message -> checks
  booking system -> checks available slots -> replies -> updates booking manually.
- **Pain points:** Lots of back-and-forth messages; staff check availability manually;
  changes happen outside office hours; easy to make booking mistakes.
- **How often:** Potentially several times a day, depending on clinic size.
- **Tools:** WhatsApp / SMS / phone / booking system / calendar.
- **Info needed:** Customer/patient, existing appointment, doctor/vet, appointment
  type, available slots, rescheduling policy.
- **Decisions:** Is the requested change allowed? Which slots are suitable? Does the
  customer need the same doctor/vet?
- **Actions:** Check appointment, find available slots, contact customer, change
  booking, notify staff/doctor.
- **Cost of inaction:** Staff time wasted, appointment slots stay unused, customers get
  frustrated, potential scheduling errors.
- **Agent opportunity:** An agent handles the conversation, checks the booking system,
  finds suitable alternatives, and updates the appointment — with staff approval for
  exceptions.

### 2. Medication Refill & Collection (Vet / Dental / Clinic / Pharmacy)

- **Problem:** Customers request medication refills over WhatsApp. Staff must
  determine whether prescription/doctor approval is required, get that approval,
  arrange payment, prepare the medication, and coordinate collection.
- **Who:** Clinic reception/admin staff, doctors/vets, pharmacy/dispensing staff,
  customers.
- **Goal:** Get a customer's medication refill processed and ready for collection.
- **Current process:** WhatsApp request -> staff check medication/prescription ->
  doctor approves if required -> arrange payment -> prepare medication -> tell
  customer when ready -> customer collects.
- **Pain points:** Lots of manual coordination; staff chase approvals; payment and
  collection happen at different stages; customers may arrive before medication is
  ready; staff need to know whether the customer is actually coming; multiple
  communication channels.
- **How often:** Potentially daily, depending on the clinic.
- **Tools:** WhatsApp / clinic system / prescription records / payment system /
  inventory / calendar.
- **Info needed:** Customer/patient, medication, previous prescription, quantity,
  doctor approval, stock availability, payment status, collection status.
- **Decisions:** Does this require doctor approval? Is the prescription still valid?
  Is medication available? Has payment been made? Is it ready for collection?
- **Actions:** Check records, request doctor approval, request payment, update
  customer, prepare collection order, notify customer, update status.
- **Cost of inaction:** Staff spend significant time coordinating; delays; customers
  make unnecessary trips; medication may not be ready; staff may miss requests.
- **Agent opportunity:** An agent coordinates the whole workflow across WhatsApp,
  clinic records, payment, and staff — while requiring human approval for medical
  decisions.

### 3. Retail Sales Promoter Stocktaking

- **Problem:** Sales promoters representing individual health/beauty brands regularly
  have to physically recount their products in stores such as NTUC and Watsons. The
  retail chain knows what has sold through its checkout/inventory system, but the
  promoter often lacks direct, real-time access to that data — so they manually check
  the shelves to answer: *"How many of MY products are physically available right
  now?"*
- **Who:** Brand sales promoters, brand representatives, merchandisers, distributors.
- **Goal:** Know current physical stock, identify what needs replenishing, restock at
  the right time, avoid overstocking/running out, record accurate stock information,
  spend less time counting and more time selling.
- **Current process:** Visit store -> find brand's shelf space -> manually count
  bottles/boxes -> record numbers -> compare with expected stock/sales -> identify
  items needing replenishment -> restock/order -> record stocktake. Repeat across
  multiple stores and SKUs.
- **Pain points:** Information gap (retailer has sales/inventory data the promoter
  can't see); physical counting of rows of similar-looking products; stock isn't
  always where expected (sold, misplaced, returned, held elsewhere); by the time the
  promoter visits, their information may already be stale.
- **Agent opportunity — a Stocktake Agent that combines multiple sources** (shelf
  image, previous stocktakes, available sales data, expected stock, store info) and
  runs a see -> compare -> detect anomalies -> reason -> recommend -> act -> follow up
  loop:
  1. **See:** use the phone camera/computer vision to identify products and count
     visible stock.
  2. **Compare:** physical count against expected/previous stock.
  3. **Detect anomalies:** e.g. "Expected 18, I can see 11," "3 products appear to be
     on the wrong shelf," "this SKU has fallen faster than usual."
  4. **Reason:** estimate whether replenishment is needed.
  5. **Recommend:** e.g. "Restock 8 units."
  6. **Act:** prepare a restock/order request, update the stocktake record, notify the
     salesperson/brand/distributor, generate a store report.
  7. **Follow up:** track "you restocked 10 units on Monday; based on recent sales,
     revisit this store by Thursday."

### 4. Supporting People With Hoarding Behaviour

- **Problem:** People experiencing hoarding behaviour may have large numbers of
  possessions and difficulty deciding what to keep, discard, donate, sell, or move —
  potentially hundreds or thousands of small, emotionally difficult decisions, plus
  practical issues like finding objects, duplicate purchases, blocked spaces, and
  safety hazards.
- **Who:** People experiencing hoarding behaviour, family members/caregivers, social
  workers, community support organisations, professional organisers/cleaning teams,
  healthcare/support professionals.
- **Goal:** Make decisions about possessions, recover usable living space, find
  important belongings, avoid buying duplicates, track progress over time, identify
  safety problems, make decluttering less overwhelming.
- **Current process:** Manual sorting, conversations with family/support workers,
  physical cleaning sessions, before/after photos, checklists, personal judgement.
- **Pain points:** Every item can require a separate decision; decisions can be
  emotionally difficult; large tasks become overwhelming; progress is hard to see;
  important items may be buried; people forget what they already own; family/support
  workers may not know where to start; safety issues may not be obvious.
- **Tools:** Phone camera/photos, messaging, spreadsheets, checklists, cloud storage,
  AI vision.
- **Info needed:** Photos/video of spaces, objects and categories, previous decisions,
  user preferences, important possessions, progress over time, safety concerns,
  support-worker observations.
- **Decisions:** Keep? Discard? Donate? Sell? Recycle? Store elsewhere?
  Photograph/document before letting go? Deal with later? Is this area becoming
  unsafe? What should be worked on next?
- **Possible agentic solutions** (ranked by interest level on the board):
  - **Decision Support Agent** — instead of "throw this away," the agent asks
    questions and helps the person reach their own decision: object -> questions ->
    understand significance -> suggest options -> record decision (keep / donate /
    sell / recycle / photograph & release / decide later).
  - **Personal Possessions Agent** — observes photos of possessions, identifies
    objects/categories, remembers where things were seen, and answers "do I already
    own a rice cooker?" — addressing "I don't know what I already have, so I buy/keep
    more."
  - **Clutter Progress Agent** — observes photos of the same room over time, tracks
    visible floor/usable surfaces/clutter level, and reports progress (e.g. "bedroom
    floor visibility increased from 31% to 38%"), suggesting the next small task.
  - **Digital Memory Agent** — turns an object into a photograph + story + digital
    memory (who/what it's associated with, date/context, audio recording), so the
    physical object can be considered separately from the memory attached to it.

## Cross-Cutting Pattern: Mapping to the agents42 Core

The starter kit's reusable shape (per the design spec) is: intake -> structured
extraction -> deterministic + judgment-based validation -> human-review interrupt on
exceptions -> export/action. Checking the four brainstormed problems against that
shape:

**Fits the extraction -> validation -> human-review -> export/act shape directly:**
- *Appointment rescheduling:* intake (WhatsApp message) -> extract (requested change,
  patient, appointment) -> decide (is this allowed? which slots fit?) -> act (rebook)
  with staff approval for exceptions -> confirm.
- *Medication refill:* intake (WhatsApp request) -> extract (medication, quantity,
  patient) -> decide (needs doctor approval? stock? payment?) -> human-in-loop
  approval gate for anything medical -> act (prepare, notify) -> confirm.
- *Retail stocktaking:* intake (shelf photo) -> extract (counted stock, via vision) ->
  decide (compare to expected, flag anomalies) -> recommend restock -> act
  (update record/notify) with human sign-off on large deviations.

**Needs a different shape (conversational / decision-support, not a single-record
pipeline):**
- *Hoarding support:* there's no single structured document to extract per "case" —
  the agent opportunity here (Decision Support Agent, Personal Possessions Agent) is a
  stateful Q&A/memory agent that accumulates observations across many turns and
  sessions, not a validator judging one record. This is the concrete case the design
  spec's Open Risks section already anticipated: *"If the real SME problem at kickoff
  doesn't fit the extraction-validation-export shape at all ... the
  Supervisor/specialist-agent pattern still applies, but the specific
  Extractor/Validator agents will be replaced wholesale."*

## What To Listen For At Kickoff

A quick checklist for classifying whichever real problem is assigned on 5 Sep, based
on the patterns above:

- Is there one canonical structured record produced per case (invoice, appointment,
  order)? -> the Extractor/Validator/Export shape applies with minimal change.
- Is the "record" actually an ongoing conversation or relationship rather than a
  single document (support, coaching, community management)? -> keep the Supervisor
  pattern, but expect the specialist nodes to look more like the Decision
  Support/memory agents above.
- Does the decision require domain expertise a human must sign off on (medical,
  legal, financial approval)? -> the human-review interrupt node is directly reusable
  regardless of domain.
- Is perception (vision/OCR) a bigger part of "extraction" than parsing text? -> the
  Extractor node's LLM call is swappable for a multimodal one without changing the
  graph shape.

## Not Yet Filled In

The Miro board's later sections ("Generate solutions", "Generate action plan") were
still blank templates as of this brainstorm session. They're now filled in — as
practice material, not a committed direction — in
`docs/2026-08-27-sme-solutions-and-action-plan.md`.
