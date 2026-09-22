# Escalation actually gets flagged for the owner, not just said in chat

Not automated via `run.py` - verifying this needs checking the owner dashboard's rendered state
(a real side effect), which the current runner only checks conversation text/tool-call counts
against, not backend state. Manual procedure instead.

## Manual: escalation trigger produces both the reply and a persisted record

1. Message the agent something that should escalate per SKILL.md's "Rules" (e.g. "can I get a 50%
   discount", "I want a refund for a botched service", a complaint, or an ambiguous request that
   doesn't resolve after one clarifying question).
2. **Expected reply:** says plainly that the business owner will follow up - not attempting to
   resolve the request itself, not inventing a policy answer.
3. **Verify the escalation was actually recorded**, not just said: `curl -u owner:<password>
   http://<dashboard-host>:8091/` (or open it in a browser) and confirm the request appears under
   "Attention" with a sensible `reason`/`detail` and, if the agent had already identified the
   customer, their name/phone shown inline.

This is checking a real reliability gap that was caught during development, not a hypothetical:
an earlier version of `SKILL.md` had the `flag_attention.py` call buried as a parenthetical aside
inside a prose "Rules" bullet, and empirically the agent gave the correct-sounding reply *without
ever actually running the script* - confirmed via `openclaw sessions tail --session-key <key>`
showing zero tool-call events for that turn, and the escalation absent from the dashboard
afterward. Restructuring it into its own explicit numbered "Escalating to the Business Owner"
procedure (matching the style of "Main Plan"/"Manage an Existing Booking", both of which *do*
reliably trigger script calls) fixed it - confirmed 3/3 across separate sessions and different
trigger phrasings (discount x2, refund complaint x1) after the change, versus the original
phrasing's one observed attempt producing no script call at all. Re-run this manual check after
any future edit to that section, the same way `06_greeting_reliability.md` exists because
"worked once" and "works reliably" turned out to be different things for greetings too.
