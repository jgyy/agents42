# Cancel an existing booking

```json
{
  "session_key": "cancel-no-booking",
  "description": "cancel request for a customer with no existing booking says so plainly, doesn't invent one",
  "messages": [
    "Hi, my number is 90008888, please cancel my grooming appointment"
  ],
  "expect": [
    {
      "turn": 0,
      "contains_any": ["don't have", "no booking", "no upcoming", "not on file", "couldn't find"],
      "not_contains": ["cancelled", "cancelled your", "all set", "done"],
      "min_tool_calls": 1
    }
  ]
}
```

Checks the phone-with-no-booking path specifically: `list_bookings.py` returning `"bookings": []`
must not be papered over with an invented cancellation - the agent needs to say plainly there's
nothing on file.

## Manual: happy-path cancel (creates a real Calendar change)

Not automated - needs a real existing booking and has real side effects (deletes a real Calendar
event). From a phone number with an existing confirmed booking (e.g. one made via `03_booking.md`'s
manual happy-path, or a real earlier test booking):

1. "Please cancel my appointment" - the agent should look up the booking and confirm which one it
   means (service + date/time), not cancel blind on the first message.
2. **Expected:** the agent asks for explicit confirmation before acting ("just to confirm, you'd
   like to cancel your Full Grooming appointment on [date]?") - it must not cancel on the first
   message alone, since this is destructive and can't be undone through the skill.
3. Confirm ("yes, cancel it").
4. **Expected:** confirmation stating what was cancelled (service + date/time), not just "done".

Verify directly against the backend: `curl http://127.0.0.1:8090/bookings/<id>` should show
`"status": "cancelled"` on the *same* booking id - not a deleted row. Check the real Google
Calendar: the event should be gone. Also check `GET /customers/<id>/bookings` no longer lists it.

Also worth trying by hand: send a vague message ("I might not be able to make it") and confirm the
agent asks whether you actually want to cancel rather than assuming so from an ambiguous
statement; and attempting to cancel a booking that was already cancelled in an earlier step
(should reject cleanly, not error obscurely).
