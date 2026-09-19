# Fully booked day / Calendar unavailable (manual)

Both of these depend on system state that isn't safe or easy to script deterministically
(filling a real calendar day, or actually taking the backend down mid-conversation), so
they're manual procedures rather than an automated `run.py` case.

## Fully booked day

1. Pick or create a day with zero availability (check first:
   `curl -X POST http://127.0.0.1:8090/availability/search -H 'Content-Type: application/json' -d '{"business_id":"demo-groomer","service":"full_grooming","date":"<date>"}'`
   should return `"slots": []`).
2. Ask the agent to book that day.

**Expected:** it says nothing's available and offers to check a different date/time - it
must not invent a time, and must not say "let me check" and then go silent (see
DEPLOYMENT.md's note on the WhatsApp repeated-reply bug if it seems stuck rather than
genuinely saying no).

## Calendar unavailable

1. Temporarily make Calendar unreachable, e.g. rename the token file on the server:
   `mv credentials/calendar_token.json credentials/calendar_token.json.bak`, restart the
   backend (`docker compose restart app`).
2. Ask the agent to check availability or book.

**Expected:** it says it can't verify availability/complete the booking right now and
offers to have the business follow up - it must **not** say a booking is confirmed.
Verify no new row was created: `curl http://127.0.0.1:8090/bookings/<any-guessed-id>`
should 404, or check the DB directly.
3. Restore the token file and restart the backend afterward -
   `mv credentials/calendar_token.json.bak credentials/calendar_token.json` -
   don't leave this broken.
