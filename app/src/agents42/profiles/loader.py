"""Loads a business's rules (services, hours, booking policy) from its YAML
profile. This is the *only* place business-specific configuration is allowed
to live - the agent's generic behaviour (SKILL.md) and the scheduling code
must stay business-agnostic so a new business is "add one YAML file", not
"edit the core".
"""

from datetime import time
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from agents42.config import settings


class ServiceProfile(BaseModel):
    duration_minutes: int
    turnaround_minutes: int


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
    opening_hours: dict[str, OpeningHours]  # keyed by lowercase weekday name, e.g. "monday"
    required_customer_fields: list[str] = Field(default_factory=lambda: ["name", "phone"])
    optional_customer_fields: list[str] = Field(default_factory=list)
    slot_interval_minutes: int = 60


class UnknownBusinessError(LookupError):
    pass


class InvalidBusinessProfileError(ValueError):
    pass


def load_business_profile(business_id: str, businesses_dir: Path | None = None) -> BusinessProfile:
    directory = businesses_dir or settings.businesses_dir
    path = Path(directory) / f"{business_id}.yaml"
    if not path.exists():
        raise UnknownBusinessError(f"No business profile found for {business_id!r} at {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    try:
        return BusinessProfile.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError etc.
        raise InvalidBusinessProfileError(f"Invalid business profile {path}: {exc}") from exc
