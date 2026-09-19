# New vs. returning customer

A new phone number with a stated booking intent should trigger the
`needs_name` flow, not silently create a customer or refuse. Use a
**throwaway test phone number** each run (see note below) so this doesn't
collide with real customer records.

```json
{
  "session_key": "new-customer",
  "description": "new phone -> asks for name; does not invent one",
  "messages": [
    "Hi, my number is 90009111, I'd like to book a full grooming appointment"
  ],
  "expect": [
    {"turn": 0, "contains_any": ["name", "who am I", "may I have"], "min_tool_calls": 1}
  ]
}
```

**Manual follow-up** (not automated - needs a real customer that already exists):
message again from a phone number you've booked with before (e.g. the number used in
`app/tests/test_api.py` or a prior live test) and confirm the agent recognises you
without asking for your name again, and doesn't create a duplicate customer. Check via
`curl -X POST http://127.0.0.1:8090/customers/resolve -H 'Content-Type: application/json' -d '{"phone": "<number>"}'`
(no `name` field) that `"found": true` and the name matches what was set the first
time, not whatever name (if any) was mentioned in the second conversation.
