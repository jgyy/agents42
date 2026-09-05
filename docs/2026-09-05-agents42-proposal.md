# Agents42 Proposal — AI Front-Desk Manager for Appointment-Based SMEs

**Team Code:** Y5FYMK78

Source: [Google Doc](https://docs.google.com/document/d/14TV_0VPAnsPvho1htiWddkqrp0oMzZaS1PW723lo7xw)

> **Legend (from the original doc):**
> - 🟡 **Yellow** — items that need to be filled in for the official proposal.
> - 🔵 **Blue** — items for our own blueprint (not part of the official submission).

---

## Possible Problem Statements

### Quotation Preparation
Sales administrators in wholesale and distribution companies are responsible for preparing quotations for customer enquiries. Whenever customers request pricing for multiple products, staff manually search product catalogs, verify prices, calculate discounts, and prepare quotation documents. The process is repetitive, time-consuming, and heavily dependent on staff experience, resulting in inconsistent pricing, delayed responses, and lost sales opportunities during busy periods.

### Supplier Comparison
Procurement executives regularly receive quotations from multiple suppliers for the same products. Before every purchase, they manually compare prices, delivery lead times, payment terms, and supplier performance using spreadsheets and emails. As the number of suppliers and products grows, the comparison process becomes increasingly difficult, resulting in slower purchasing decisions and missed opportunities to negotiate better terms.

### Managing WhatsApp Sales Enquiries
Sales representatives receive a high volume of customer enquiries through WhatsApp every day. Many questions relate to product availability, pricing, delivery charges, operating hours, and other frequently requested information. Responding individually to repetitive enquiries consumes a significant portion of the workday, delaying responses to high-value customers and reducing the team's ability to focus on sales activities.

### Product Selection
Customers visiting an online store often struggle to identify products that best meet their needs because product information is spread across multiple pages and technical specifications are difficult to compare. Many customers leave without purchasing or contact the sales team for assistance, increasing the workload of sales representatives and reducing online conversion rates.

### Inventory Monitoring
Inventory managers are responsible for ensuring adequate stock while avoiding excess inventory. Stock levels are typically reviewed using spreadsheets or periodic reports, making it difficult to identify slow-moving products, fast-selling items, or potential shortages early. As product ranges increase, inventory planning becomes increasingly reactive, leading to stockouts, overstocking, and unnecessary storage costs.

### Purchase Order Preparation
Purchasing officers regularly monitor inventory levels and prepare purchase orders for suppliers. Inventory checks are performed manually across multiple systems or spreadsheets before deciding what to reorder. This repetitive process is prone to oversight, particularly during busy periods, increasing the likelihood of delayed purchases and inventory shortages.

### Production Planning
Production planners in manufacturing SMEs coordinate customer orders, available inventory, machine capacity, and workforce schedules to determine daily production activities. Planning is often performed manually using spreadsheets and historical experience, making it difficult to adjust quickly to changing customer demand, urgent orders, or material shortages.

### Technician Scheduling
Service coordinators assign technicians to customer jobs based on availability, location, skillset, and urgency. As service requests increase throughout the day, manually balancing technician workloads becomes increasingly complex. Inefficient scheduling often results in excessive travel, delayed appointments, and uneven utilization of technicians.

### Service Report Preparation
Field technicians are required to prepare service reports after completing customer visits. They manually document work performed, replacement parts used, customer observations, and follow-up recommendations, often after travelling to their next job. Preparing reports consumes valuable time and frequently results in incomplete or inconsistent documentation.

### Client Document Collection
Accounting firms require clients to submit invoices, receipts, bank statements, and supporting documents before monthly bookkeeping can begin. Many clients submit incomplete information or respond only after repeated reminders. Account managers spend considerable time following up with clients, delaying bookkeeping work and extending financial closing cycles.

---

## Selected Problem Statement

*(Select one from above — can't be edited.)*

### Managing WhatsApp Sales Enquiries
Sales representatives receive a high volume of customer enquiries through WhatsApp every day. Many questions relate to product availability, pricing, delivery charges, operating hours, and other frequently requested information. Responding individually to repetitive enquiries consumes a significant portion of the workday, delaying responses to high-value customers and reducing the team's ability to focus on sales activities.

---

## Scope and Proposed Approach

Define a staged scope with explicit assumptions and estimates. The scope is staged so that each week produces a usable decision or increment.

| Milestone | Scope / Deliverables | Assumptions | Estimated Effort (days) |
|---|---|---|---|
| 1st Week Scope | 🟡 _Enter scope/deliverables..._ | 🟡 _Enter assumptions to your scope..._ | 🟡 |
| 2nd Week Scope | 🟡 | 🟡 | 🟡 |
| Final Scope | Build a working prototype of an AI front-desk manager for appointment-based SMEs. The system will handle customer enquiries, understand service requirements, check live availability, create and modify bookings, and support business owners in managing schedule changes through natural-language instructions. The final prototype will demonstrate both customer-facing and owner-facing interactions, including a scenario where a change in staff availability affects existing appointments and the agent helps coordinate rescheduling. | 🟡 | 🟡 |

---

## Proposed Solution Overview

The proposed solution is an AI service agent designed for appointment-based SMEs that rely heavily on WhatsApp for customer communication and scheduling.

The agent acts as both a front-facing customer service representative and an internal scheduling assistant.

- Customers can ask questions about services, pricing and availability, and the agent can guide them through booking, cancellation and rescheduling.
- The agent is connected to the business schedule and customer records, allowing it to provide context-aware responses rather than generic FAQ replies.
- Business owners can also communicate with the agent using natural language to manage their schedule, review appointments and handle changes in availability.
- When a change affects existing customers, the agent can identify affected appointments, suggest alternatives and prepare or send rescheduling messages.
- Human approval is required for exceptions, ambiguous situations, sensitive customer issues and high-impact schedule changes.

---

## Delivery, Measurement and Controls

Show how the proposal will work safely and how success will be measured.

### Data, Tools & Operating Constraints

| Data or Document | Source | Owner | Access Status | Privacy or Quality Concern |
|---|---|---|---|---|
| Service and pricing information | Business document / database | Business owner | Available | Must be up to date |
| Operating hours | Business schedule | Business owner | Available | Holiday / special hours may differ |
| Staff availability | Calendar | Business owner / staff | Available | Personal scheduling info |
| Customer bookings | Booking database / calendar | Business | Prototype | Customer data |
| Customer profile | Management system / database | Business | Prototype | Customer contact info |
| FAQ / business policies | Business documents | Business owner | Available | Agent must not invent policies |
| Payment status | Payment system / receipts / banking screenshots / bank records | Business | KIV | Financial information |

### AI Models & Tools

| Model / Tool | Role in the Proposal | Operating Constraint |
|---|---|---|
| LLM — 🟡 _specific?_ | Understand customer/owner messages and reason about next action | Avoid hallucination |
| Agent framework — openclaw | Manage workflow and tool calls | Only authorised tools/actions |
| Messaging interface | Customer / owner communication | Need WhatsApp and other services |
| Knowledge base / RAG | Retrieve FAQ, pricing and service policies | Only use approved business content |
| Calendar API / database | Retrieve and update appointments | Avoid double booking |
| Customer database | Store customer profile / history | Personal data |
| API 🟡 _?_ | Execute booking and workflow actions | Validate inputs before write operations |

### Agent / Workflow Roles

| Role | Responsibility | Input | Output | Escalate When |
|---|---|---|---|---|
| Customer Service Agent | Understand enquiry and answer questions | Customer message | Answer / next question / message | Cannot answer confidently / unable to understand request |
| Scheduling Agent | Check availability and manage bookings | Service, date, preferences, customer ID | Valid booking options | Conflict or no suitable slot |
| Owner Assistant Agent | Answer owner questions and carry out instructions | Owner message | Schedule action / summary | High-impact or ambiguous change |
| Customer Follow-up Agent | Confirmation / reminder / follow-up | Booking / customer history | Reminder / follow-up message | Complaint or unusual response |
| Rescheduling Coordinator | Identify affected appointments and find alternatives | Schedule change / customer message | Alternatives + customer messages | No suitable alternatives |

> Can be separate LLM agents or multiple roles in one / several.

### Integrations and Manual Fallback

Describe integrations and fallback if one fails.

- The prototype will integrate with a scheduling/calendar system, customer records and a structured business knowledge base.
- Messaging may be implemented through a simulated WhatsApp-style interface during development, with future integration to the WhatsApp Business API.
- If the AI service is unavailable, customers can be redirected to a human contact.
- If the calendar integration fails, the agent must not confirm new bookings and should instead inform the customer that availability needs to be checked manually.
- If the agent is unable to confidently interpret a request, it should escalate the conversation to the business owner rather than guess.
- Existing calendar and scheduling interfaces remain available so the owner is never dependent on the AI agent alone (owner can still access their current workflow).

### Success Measures

| Metric (what we are measuring) | Baseline (current level) | Target (goal to reach) | How Measured (method) | Review Period |
|---|---|---|---|---|
| Time spent handling routine enquiries | Manual, person managing | Reduce by 50% | Compare staff time before/after | Prototyping |
| Average response time | Depends on hours and staff availability | < 1 minute for routine enquiries | Message timestamps | Test period |
| Valid booking suggestion | Manual success rate | ≥ 95% valid bookings | Compare bookings against rules/calendar | Test scenarios |
| Human involvement | 100% manual | < 30% for routine cases | Records and logs | Test period |
| Time to resolve schedule disruption | 100% manual | 50% improvement | Timed scenario test | Demo / test |
| Customer task completion | Manual success rate | ≥ 80% complete booking without human | User testing | Test period |

### Risks, Guardrails and Human Approval

| Risk (what can go wrong) | Consequence (what if it does) | Preventive Control (how to prevent) | Human Owner (who's responsible) |
|---|---|---|---|
| Agent gives wrong info | 🟡 | 🟡 | 🟡 |
| Invalid booking | 🟡 | 🟡 | 🟡 |
| Agent misidentifies availability | 🟡 | 🟡 | 🟡 |
| Wrong scheduling | 🟡 | 🟡 | 🟡 |
| Personal data exposure | 🟡 | 🟡 | 🟡 |
| Owner instruction affects the wrong customers | 🟡 | 🟡 | 🟡 |
| Agent sends inappropriate message | 🟡 | 🟡 | 🟡 |
| Customer makes unusual request | 🟡 | 🟡 | 🟡 |
| Customer prompt injection | 🟡 | 🟡 | 🟡 |

### Human Approval Points

Human approval will be required when:

1. The agent cannot confidently interpret a customer request.
2. A scheduling change affects multiple existing appointments.
3. No suitable replacement slot can be found.
4. A customer requests an exception to normal business policy.
5. Refunds, discounts or payment disputes are involved.
6. The agent detects a complaint or potentially sensitive issue.
7. A bulk customer message is about to be sent.
8. The business owner has not explicitly authorised the agent to make a particular type of schedule change.

**Example:**

> Owner says: "Staff Y is unavailable tomorrow afternoon."
>
> Agent checks calendar → finds 4 affected customers → finds possible replacement slots → drafts new options → owner approves → agent contacts customers → updates confirmed bookings → reports back.

---

**END of official proposal**

---

## 🔵 Our Blueprint (internal, not part of the official proposal)

### Our Unique Problem Context / Pain Points

**Managing Customer Enquiries and Scheduling for Time-Sensitive Service SMEs**

Small service businesses and independent service providers, such as home cleaning services, private instructors, personal trainers, driving instructors, dental clinics, beauty services, mobile pet groomers, and massage services, often manage customer enquiries and appointments through WhatsApp.

Unlike simple product enquiries, these conversations are time-sensitive and context-dependent. Customers ask about availability, prices, service options, preferred staff, locations, rescheduling and payment. Staff must understand each customer's needs, check the latest schedule, identify suitable time slots, answer questions, confirm appointments and update the schedule.

Changes such as cancellations, delays and rescheduling create further work because one change may affect multiple customers and available time slots.

For small businesses, the person responding to WhatsApp may also be the owner or service provider. Time spent coordinating appointments is therefore time taken away from delivering the service or growing the business. Every minute spent negotiating schedules, answering basic questions, and tracking payments is a direct loss of income.

### Our Unique Problem Statement

> How might we create an intelligent service agent that acts as both a front-facing customer service representative and scheduling manager, communicating naturally with customers while understanding and managing the business's real-time schedule?

### Goal

> "We are building an AI front-desk manager for appointment-based SMEs. The agent can talk to customers naturally, understand the live schedule, manage bookings and help the business owner run their day."

### Possible Features

**Towards the customer**, the agent could:

- Answer common and context-specific customer questions.
- Understand what service the customer needs and gather missing information.
- Check real-time availability and suggest suitable appointments.
- Book, cancel and reschedule appointments according to business rules.
- Handle preferences such as a particular instructor, therapist or service provider.
- Recognise scheduling conflicts and changes that require attention.
- Escalate unusual requests or important customers to a human.
- Keep the business's existing scheduling workflow updated and accessible.

**Towards the business owner**, allowing them to communicate with it naturally:

- "Move my 3pm appointment tomorrow."
- "Who hasn't confirmed their appointment this weekend?"
- "I'm unavailable Tuesday afternoon. Help me rearrange the affected customers."
- "Find me an available slot for Sarah next week."
