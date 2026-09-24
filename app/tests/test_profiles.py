from datetime import time
from pathlib import Path

import pytest

from agents42.profiles.loader import InvalidBusinessProfileError, UnknownBusinessError, load_business_profile

BUSINESSES_DIR = Path(__file__).resolve().parents[2] / "businesses"

MINIMAL_PROFILE_WITH_HOURS = """\
id: hours-check
name: Hours Check
timezone: Asia/Singapore
calendar_id: primary
services:
  trim: {{ duration_minutes: 60, turnaround_minutes: 0 }}
opening_hours:
  monday: {{ open: {open}, close: {close} }}
"""


def test_groomer_profile_loads():
    profile = load_business_profile("demo-groomer", businesses_dir=BUSINESSES_DIR)

    # Structural checks only - name/address are expected to be edited per-business
    # without needing a test update every time.
    assert profile.id == "demo-groomer"
    assert profile.name
    assert profile.timezone == "Asia/Singapore"
    assert profile.services["full_grooming"].duration_minutes == 120
    assert profile.services["full_grooming"].turnaround_minutes == 60
    assert profile.opening_hours["monday"].open == time(9, 0)
    assert profile.opening_hours["monday"].close == time(18, 0)
    assert "sunday" not in profile.opening_hours


def test_unknown_business_raises(tmp_path):
    with pytest.raises(UnknownBusinessError):
        load_business_profile("does-not-exist", businesses_dir=tmp_path)


@pytest.mark.parametrize("business_id", ["../evil", "sub/dir", "demo-groomer.yaml", "", "a b", "..", "dem*"])
def test_business_id_must_be_a_plain_slug(tmp_path, business_id):
    """business_id is user-controlled (it comes off the wire), so it must
    never be allowed to escape businesses_dir or address arbitrary files.
    """
    businesses = tmp_path / "businesses"
    businesses.mkdir()
    # A valid profile one level *above* businesses_dir - must be unreachable.
    (tmp_path / "evil.yaml").write_text((BUSINESSES_DIR / "demo-groomer.yaml").read_text())
    with pytest.raises(UnknownBusinessError):
        load_business_profile(business_id, businesses_dir=businesses)


def test_quoted_yaml_opening_hours_load_as_written(tmp_path):
    (tmp_path / "hours-check.yaml").write_text(MINIMAL_PROFILE_WITH_HOURS.format(open='"09:30"', close='"18:00"'))

    hours = load_business_profile("hours-check", businesses_dir=tmp_path).opening_hours["monday"]

    assert hours.open == time(9, 30)
    assert hours.close == time(18, 0)


@pytest.mark.parametrize("open_, close", [("09:00", "18:00"), ("9:30", "15:00")])
def test_unquoted_yaml_opening_hours_are_rejected_not_misread(tmp_path, open_, close):
    """PyYAML follows YAML 1.1, where an unquoted 18:00 is the base-60
    integer 1080 - and pydantic reads an int as seconds since midnight, so
    close would silently become 00:18 UTC and every day would have zero
    slots, indistinguishable from "fully booked". A new business is "add a
    YAML file", so this must fail loudly at load time instead.
    """
    (tmp_path / "hours-check.yaml").write_text(MINIMAL_PROFILE_WITH_HOURS.format(open=open_, close=close))

    with pytest.raises(InvalidBusinessProfileError, match="quote"):
        load_business_profile("hours-check", businesses_dir=tmp_path)


MINIMAL_PROFILE_WITH_NUMBERS = """\
id: numbers-check
name: Numbers Check
timezone: Asia/Singapore
calendar_id: primary
services:
  trim: {{ duration_minutes: {duration}, turnaround_minutes: {turnaround} }}
opening_hours:
  monday: {{ open: "09:00", close: "18:00" }}
slot_interval_minutes: {interval}
max_advance_days: {advance}
"""
# The smallest legitimate value of each field: 0 turnaround is back-to-back
# bookings, 0 max_advance_days is "today only".
SMALLEST_VALID_NUMBERS = {"duration": 1, "turnaround": 0, "interval": 1, "advance": 0}


def test_smallest_valid_profile_numbers_load(tmp_path):
    (tmp_path / "numbers-check.yaml").write_text(MINIMAL_PROFILE_WITH_NUMBERS.format(**SMALLEST_VALID_NUMBERS))

    profile = load_business_profile("numbers-check", businesses_dir=tmp_path)

    assert profile.slot_interval_minutes == 1
    assert profile.max_advance_days == 0
    assert profile.services["trim"].duration_minutes == 1
    assert profile.services["trim"].turnaround_minutes == 0


@pytest.mark.parametrize(
    "field, value, error_field",
    [
        ("interval", 0, "slot_interval_minutes"),
        ("interval", -60, "slot_interval_minutes"),
        ("duration", 0, "duration_minutes"),
        ("duration", -60, "duration_minutes"),
        ("turnaround", -60, "turnaround_minutes"),
        ("advance", -1, "max_advance_days"),
    ],
)
def test_out_of_range_profile_numbers_are_rejected(tmp_path, field, value, error_field):
    """find_available_slots steps by slot_interval_minutes, so 0 never
    advances (it hangs, appending the same slot until the worker runs out of
    memory) and a negative step walks backwards until the datetime
    underflows, pinning the worker for seconds to minutes. A non-positive
    duration silently yields zero slots ("fully booked"), a negative
    turnaround shrinks the buffer below zero and offers slots overlapping an
    existing booking, and a negative max_advance_days rejects every date.
    None of those is a usable profile, so fail loudly at load time.
    """
    numbers = {**SMALLEST_VALID_NUMBERS, field: value}
    (tmp_path / "numbers-check.yaml").write_text(MINIMAL_PROFILE_WITH_NUMBERS.format(**numbers))

    with pytest.raises(InvalidBusinessProfileError, match=error_field):
        load_business_profile("numbers-check", businesses_dir=tmp_path)
