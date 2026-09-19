"""Deterministic slot math. No LLM involvement, no I/O - pure functions over
datetimes so they can be exhaustively unit tested. The LLM/agent layer is only
ever allowed to see the *output* of these functions, never to compute or
invent availability itself.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class BusyPeriod:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def is_valid_slot(
    proposed_start: datetime,
    proposed_end: datetime,
    existing_events: list[BusyPeriod],
    buffer_minutes: int,
) -> bool:
    """A slot is valid if it does not overlap any existing event, and there is
    at least `buffer_minutes` of gap on either side of every existing event.

    The buffer is applied symmetrically (as if every booking, existing or
    proposed, carries its own trailing buffer) so the 1-hour gap rule holds
    regardless of whether the proposed slot lands before or after a
    neighbouring booking.
    """
    if proposed_end <= proposed_start:
        return False

    buffer = timedelta(minutes=buffer_minutes)
    for event in existing_events:
        if _overlaps(proposed_start, proposed_end + buffer, event.start, event.end + buffer):
            return False
    return True


def find_available_slots(
    date_: datetime.date,
    opening_time: time,
    closing_time: time,
    existing_events: list[BusyPeriod],
    duration_minutes: int,
    buffer_minutes: int,
    slot_interval_minutes: int,
    timezone,
    now: datetime | None = None,
) -> list[Slot]:
    """Return every candidate slot of `duration_minutes`, on `slot_interval_minutes`
    boundaries, within [opening_time, closing_time) on `date_`, that satisfies
    `is_valid_slot` against `existing_events` and is not already in the past.
    """
    duration = timedelta(minutes=duration_minutes)
    day_open = datetime.combine(date_, opening_time, tzinfo=timezone)
    day_close = datetime.combine(date_, closing_time, tzinfo=timezone)
    reference_now = now or datetime.now(tz=timezone)

    slots: list[Slot] = []
    candidate_start = day_open
    step = timedelta(minutes=slot_interval_minutes)
    while candidate_start + duration <= day_close:
        candidate_end = candidate_start + duration
        if candidate_start >= reference_now and is_valid_slot(
            candidate_start, candidate_end, existing_events, buffer_minutes
        ):
            slots.append(Slot(start=candidate_start, end=candidate_end))
        candidate_start += step

    return slots


def filter_by_period(slots: list[Slot], period: str | None) -> list[Slot]:
    """Optionally narrow slots to "morning" (< 12:00), "afternoon" (12:00-17:00)
    or "evening" (>= 17:00), local time. `period=None` returns all slots.
    """
    if period is None:
        return slots

    def bucket(slot: Slot) -> str:
        hour = slot.start.hour
        if hour < 12:
            return "morning"
        if hour < 17:
            return "afternoon"
        return "evening"

    return [slot for slot in slots if bucket(slot) == period]
