# Greeting and business info

A bare greeting should get a short, friendly reply using real business
info - and must not create a customer record. A direct FAQ question should
be answered from `get_business_info.py`, not invented.

```json
{
  "session_key": "business-info",
  "description": "greeting does not create a customer; FAQ uses the real tool",
  "messages": [
    "hello",
    "what services do you offer and what are your hours?"
  ],
  "expect": [
    {"turn": 0, "not_contains": ["phone number", "customer_id"], "max_tool_calls": 2},
    {"turn": 1, "contains_any": ["Full Grooming", "grooming"], "min_tool_calls": 1}
  ]
}
```

Run this against a **fresh** session (the runner does this automatically via a unique
session-key suffix). If turn 0 asks for a phone number or otherwise clearly tries to
identify/register the customer, that's the "hello creates a DB row" regression this
case exists to catch - see SKILL.md's "Greeting / General Enquiry" section.
