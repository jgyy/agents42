from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from agents42.scheduling.service import BusyPeriod, filter_by_period, find_available_slots, is_valid_slot

TZ = ZoneInfo("Asia/Singapore")
DAY = date(2026, 9, 18)  # a Friday


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 18, hour, minute, tzinfo=TZ)


def test_free_slot_is_valid_with_no_existing_events():
    assert is_valid_slot(dt(10), dt(12), existing_events=[], buffer_minutes=60) is True


def test_overlapping_slot_is_invalid():
    existing = [BusyPeriod(start=dt(10), end=dt(12))]
    assert is_valid_slot(dt(11), dt(13), existing, buffer_minutes=60) is False


def test_slot_inside_required_buffer_after_existing_event_is_invalid():
    # Existing grooming 10:00-12:00 + 1h buffer blocks until 13:00.
    existing = [BusyPeriod(start=dt(10), end=dt(12))]
    assert is_valid_slot(dt(12, 30), dt(14, 30), existing, buffer_minutes=60) is False


def test_slot_exactly_after_buffer_is_valid():
    existing = [BusyPeriod(start=dt(10), end=dt(12))]
    assert is_valid_slot(dt(13), dt(15), existing, buffer_minutes=60) is True


def test_slot_inside_buffer_before_existing_event_is_invalid():
    existing = [BusyPeriod(start=dt(14), end=dt(16))]
    # Proposed 12:00-13:30 would end only 30 min before the next booking starts.
    assert is_valid_slot(dt(12), dt(13, 30), existing, buffer_minutes=60) is False


def test_zero_or_negative_duration_is_invalid():
    assert is_valid_slot(dt(10), dt(10), existing_events=[], buffer_minutes=60) is False
    assert is_valid_slot(dt(10), dt(9), existing_events=[], buffer_minutes=60) is False


def test_find_available_slots_respects_hours_duration_and_buffer():
    # 09:00-18:00 opening, one existing 10:00-12:00 grooming, 2h duration + 1h buffer.
    existing = [BusyPeriod(start=dt(10), end=dt(12))]
    slots = find_available_slots(
        DAY,
        opening_time=time(9, 0),
        closing_time=time(18, 0),
        existing_events=existing,
        duration_minutes=120,
        buffer_minutes=60,
        slot_interval_minutes=60,
        timezone=TZ,
        now=dt(0),
    )
    starts = [s.start.hour for s in slots]
    assert 9 not in starts  # would overlap the 10-12 buffer window backwards? still check no overlap
    assert 10 not in starts  # overlaps existing event
    assert 11 not in starts  # inside existing event
    assert 12 not in starts  # inside the required 1h buffer after the event
    assert 13 in starts  # first valid slot after buffer
    assert all(s.end.time() <= time(18, 0) for s in slots)


def test_find_available_slots_excludes_past_slots():
    slots = find_available_slots(
        DAY,
        opening_time=time(9, 0),
        closing_time=time(18, 0),
        existing_events=[],
        duration_minutes=120,
        buffer_minutes=60,
        slot_interval_minutes=60,
        timezone=TZ,
        now=dt(11, 30),
    )
    assert all(s.start >= dt(11, 30) for s in slots)
    assert dt(10) not in [s.start for s in slots]


def test_filter_by_period():
    from agents42.scheduling.service import Slot

    slots = [
        Slot(start=dt(9), end=dt(11)),
        Slot(start=dt(13), end=dt(15)),
        Slot(start=dt(18), end=dt(20)),
    ]
    assert filter_by_period(slots, "morning") == [slots[0]]
    assert filter_by_period(slots, "afternoon") == [slots[1]]
    assert filter_by_period(slots, "evening") == [slots[2]]
    assert filter_by_period(slots, None) == slots


def test_exclude_period_carves_own_interval_out_of_merged_busy_ranges():
    """Google freebusy merges overlapping events into one range, so a
    booking's own slot can arrive glued to a neighbour's. Subtracting the
    own interval must keep every leftover piece, not drop the whole range.
    """
    from agents42.scheduling.service import exclude_period

    day = datetime(2026, 10, 2, tzinfo=TZ)
    at = lambda h: day.replace(hour=h)  # noqa: E731

    merged = [BusyPeriod(at(9), at(13))]  # own 09-11 merged with a neighbour 11-13
    assert exclude_period(merged, at(9), at(11)) == [BusyPeriod(at(11), at(13))]

    surrounded = [BusyPeriod(at(8), at(14))]  # own 10-12 inside a bigger range
    assert exclude_period(surrounded, at(10), at(12)) == [BusyPeriod(at(8), at(10)), BusyPeriod(at(12), at(14))]

    exact = [BusyPeriod(at(9), at(11)), BusyPeriod(at(15), at(16))]
    assert exclude_period(exact, at(9), at(11)) == [BusyPeriod(at(15), at(16))]

    untouched = [BusyPeriod(at(15), at(16))]
    assert exclude_period(untouched, at(9), at(11)) == untouched
