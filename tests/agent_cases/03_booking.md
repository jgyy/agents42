# Booking: invalid requests are rejected, never confirmed

These don't depend on live calendar state, so they're safe to run repeatedly - the
backend must reject both regardless of what's actually free (see
`app/tests/test_api.py::test_booking_rejects_start_not_on_slot_interval` and
`::test_booking_rejects_past_start_time` for the equivalent backend-only checks).
The point here is confirming the *agent* never tells the customer it worked.

```json
{
  "session_key": "booking-invalid",
  "description": "misaligned time and past date are both rejected, never confirmed",
  "messages": [
    "Hi I'm Test User, my number is 90009222, can you book me for full grooming today at 1:37pm?",
    "ok how about full grooming last Monday at 9am instead?"
  ],
  "expect": [
    {"turn": 0, "not_contains": ["confirmed", "booked!", "all set", "you're set"]},
    {"turn": 1, "not_contains": ["confirmed", "booked!", "all set", "you're set"]}
  ]
}
```

## Manual: happy-path booking (creates a real booking + Calendar event)

Not automated here since it has real side effects and depends on live availability.
From a fresh test phone number, walk through: state booking intent -> give a name if
asked -> ask for a real available date ("next Monday morning") -> confirm one of the
*exact* offered slots -> confirm the booking. Verify with
`curl http://127.0.0.1:8090/bookings/<id>` that `status` is `"confirmed"` and
`google_event_id` is set, and check the real Google Calendar for the event.

Also worth trying by hand: ask for a slot you weren't offered ("actually the 1pm one" when
only 9am/10am/11am were listed) - the agent should go back to a real availability search for
that time, not just accept it.
