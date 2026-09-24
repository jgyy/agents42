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
