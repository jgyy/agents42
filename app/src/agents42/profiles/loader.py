"""Loads a business's rules (services, hours, booking policy) from its YAML
profile. This is the *only* place business-specific configuration is allowed
to live - the agent's generic behaviour (SKILL.md) and the scheduling code
must stay business-agnostic so a new business is "add one YAML file", not
"edit the core".
"""

import re
from datetime import time
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from agents42.config import settings


class ServiceProfile(BaseModel):
    # Bounded so a typo fails at load instead of silently breaking search:
    # duration <= 0 yields zero slots ("fully booked"), and turnaround < 0
    # shrinks the buffer below zero, offering slots that overlap a booking.
    duration_minutes: int = Field(gt=0)
    turnaround_minutes: int = Field(ge=0)  # 0 = back-to-back bookings
    display_name: str | None = None  # falls back to a humanized key if unset - see api.py
    price_from: str | None = None  # e.g. "S$60" - a starting estimate, not a computed price


class AddOnProfile(BaseModel):
    """A priced extra that isn't independently bookable - no duration/
    turnaround of its own, so it can't go through search_availability/
    create_booking like a ServiceProfile can. The agent can quote its price
    if asked, but must not try to book one as a standalone appointment.
    """

    display_name: str | None = None
    price_from: str | None = None


class OpeningHours(BaseModel):
    open: time
    close: time

    @field_validator("open", "close", mode="before")
    @classmethod
    def _reject_yaml_sexagesimal(cls, value):
        """PyYAML follows YAML 1.1, where an unquoted 18:00 is the base-60
        integer 1080 (and 9:30 is 570). Left alone, pydantic reads that int
        as seconds since midnight - close becomes 00:18 UTC, and every day
        silently has zero slots, which looks exactly like "fully booked".
        Reject rather than convert back: 18:00, 18:00:00 and a literal 1080
        all arrive as indistinguishable ints, so any conversion is a guess.
        """
        if isinstance(value, (int, float)):
            raise ValueError(
                f"got the number {value!r}, not a time - quote opening hours in YAML, e.g. "
                'close: "18:00" (unquoted, YAML reads h:mm as base 60, so 18:00 becomes 1080)'
            )
        return value


class BusinessProfile(BaseModel):
    id: str
    name: str
    timezone: str
    calendar_id: str
    address: str | None = None
    services: dict[str, ServiceProfile]
    add_ons: dict[str, AddOnProfile] = Field(default_factory=dict)
    opening_hours: dict[str, OpeningHours]  # keyed by lowercase weekday name, e.g. "monday"
    required_customer_fields: list[str] = Field(default_factory=lambda: ["name", "phone"])
    optional_customer_fields: list[str] = Field(default_factory=list)
    # find_available_slots steps by this, so 0 would never advance (hanging
    # the request while it appends the same slot until memory runs out) and
    # a negative step would walk backwards until the datetime underflows -
    # verified: 3.8s at -60, ~4 minutes at -1, then an unhandled 500.
    slot_interval_minutes: int = Field(default=60, gt=0)
    about: str | None = None  # credentials/qualifications/appointment-policy blurb, relayed verbatim
    pricing_note: str | None = None  # e.g. "exact cost to be advised" - shown alongside price_from figures
    # None = no limit; e.g. 90 for "up to 3 months ahead", 0 for today only.
    # Negative would reject every date, today included.
    max_advance_days: int | None = Field(default=None, ge=0)


class UnknownBusinessError(LookupError):
    pass


# business_id arrives off the wire (URL path / JSON body) and is used to build
# a filesystem path, so it must be a plain slug - never "../x", never a nested
# path, never a glob. Anything else is treated as "no such business".
_BUSINESS_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class InvalidBusinessProfileError(ValueError):
    pass


def load_business_profile(business_id: str, businesses_dir: Path | None = None) -> BusinessProfile:
    if not _BUSINESS_ID_PATTERN.fullmatch(business_id or ""):
        raise UnknownBusinessError(f"No business profile found for {business_id!r} (not a valid business id)")

    directory = businesses_dir or settings.businesses_dir
    path = Path(directory) / f"{business_id}.yaml"
    if not path.exists():
        raise UnknownBusinessError(f"No business profile found for {business_id!r} at {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    try:
        return BusinessProfile.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError etc.
        raise InvalidBusinessProfileError(f"Invalid business profile {path}: {exc}") from exc
