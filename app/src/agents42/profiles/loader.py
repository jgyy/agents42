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
from pydantic import BaseModel, Field

from agents42.config import settings


class ServiceProfile(BaseModel):
    duration_minutes: int
    turnaround_minutes: int
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
    slot_interval_minutes: int = 60
    about: str | None = None  # credentials/qualifications/appointment-policy blurb, relayed verbatim
    pricing_note: str | None = None  # e.g. "exact cost to be advised" - shown alongside price_from figures


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
