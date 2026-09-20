# Greeting reliability (not just correctness once)

`01_business_info.md` checks that *one* "hello" gets a correct reply. This case exists because
the real bug we hit wasn't "can it ever answer hello right" - it's "does it do so *consistently*"
(see DEPLOYMENT.md's "A bare greeting doesn't reliably engage the skill"). Five varied greetings,
each in its own fresh session, spaced apart to respect the hackathon gateway's rate limit.

```json
{
  "session_key": "greeting-reliability",
  "description": "5 varied bare greetings, fresh sessions, spaced 20s apart",
  "delay_seconds": 20,
  "runs": [
    {"message": "hello"},
    {"message": "hi"},
    {"message": "good morning"},
    {"message": "you there?"},
    {"message": "hello"}
  ],
  "expect_each": {
    "contains_any": ["Grooming", "grooming"],
    "not_contains": [
      "phone number", "what's your name", "may i have your name",
      "riyadh", "salon", "studio", "spa",
      "calendars, email", "calendars and meetings"
    ],
    "min_tool_calls": 1
  }
}
```

**Success condition: 5/5** - not "at least one." Each run must: respond at all, use
`get_business_info.py` (`min_tool_calls: 1` - a pure-memory reply makes zero calls), name the
real business, not invent a different business/location (the `not_contains` list includes
"Riyadh"/"Salon"/"Studio"/"Spa" specifically because a fabricated-business run once produced
"Aisha Salon... Riyadh" - see DEPLOYMENT.md), and not ask for a phone number or name (a bare
greeting is not booking intent - see SKILL.md's "Greeting / General Enquiry").

**Not automated here: "no customer record created."** Checking this precisely means knowing
which phone number (if any) the conversation used and querying the backend before/after - a bare
greeting never mentions a phone number, so there's nothing to look up. Manual spot-check: watch
`docker compose logs -f app` while running this case and confirm no `POST /customers/resolve`
call appears in the logs for any of the 5 runs.

Costs 5 real LLM calls per run of this file (against the hackathon gateway if
`AGENTS42_TEST_MODEL` is set to it) - don't run this repeatedly without reason, and definitely
don't drop `delay_seconds` to make it faster.
