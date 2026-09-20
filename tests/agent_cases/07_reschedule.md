# Reschedule an existing booking

```json
{
  "session_key": "reschedule-no-booking",
  "description": "reschedule request for a customer with no existing booking says so plainly, doesn't invent one",
  "messages": [
    "Hi, my number is 90007777, I'd like to reschedule my grooming appointment"
  ],
  "expect": [
    {
      "turn": 0,
      "contains_any": ["don't have", "no booking", "no upcoming", "not on file", "couldn't find"],
      "not_contains": ["confirmed", "moved to", "rescheduled to", "all set"],
      "min_tool_calls": 1
    }
  ]
}
```

Checks the phone-with-no-booking path specifically: `list_bookings.py` returning `"bookings": []`
must not be papered over with an invented "sure, when would you like to move it to" - the agent
needs to say plainly there's nothing on file before it can meaningfully continue.

## Manual: happy-path reschedule (creates a real Calendar change)

Not automated - needs a real existing booking and has real side effects (deletes/creates a real
Calendar event). From a phone number with an existing confirmed booking (e.g. one made via
`03_booking.md`'s manual happy-path, or a real earlier test booking):

1. "I'd like to reschedule my appointment" - the agent should look up the booking and confirm
   which one it means (service + current date/time), not just ask "when to?" blind.
2. State a new date - the agent should search fresh availability for that date, present real
   slots, and ask you to pick one of them.
3. Confirm one of the exact offered slots.
4. **Expected:** confirmation naming both the *old* time and the *new* time, not just the new one
   in isolation - a customer who wasn't watching closely should be able to tell from the message
   alone that this was a move, not a fresh booking.

Verify directly against the backend: `curl http://127.0.0.1:8090/bookings/<id>` should show the
*same* booking id with the *new* start/end and a *new* `google_event_id` - not a second booking
row. Check the real Google Calendar: the old event should be gone, the new one present.

Also worth trying by hand: reschedule to the *exact same* time the booking is already at (should
succeed - the booking's own current slot must not block itself), and asking to reschedule to an
invalid time (misaligned, in the past) - should reject the same way `03_booking.md`'s invalid
booking cases do, and the original booking must remain untouched and still confirmed.
