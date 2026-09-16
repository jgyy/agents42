"""Customer lookup/creation. Phone normalisation and dedup logic live here as
plain Python - never left to the LLM to judge whether two numbers match.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents42.models import Customer

_DEFAULT_COUNTRY_CODE = "65"  # Singapore, per the hackathon's target market


class InvalidPhoneNumber(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """Normalize a phone number to E.164-ish form, e.g. "+6591234567".

    Accepts local 8-digit Singapore numbers (assumes +65), numbers already
    prefixed with a country code, and numbers with spaces/dashes/brackets.
    """
    digits = re.sub(r"[^\d+]", "", raw or "")
    if not digits:
        raise InvalidPhoneNumber(f"Empty phone number: {raw!r}")

    if digits.startswith("+"):
        normalized = digits
    elif digits.startswith("00"):
        normalized = "+" + digits[2:]
    elif len(digits) == 8:
        normalized = f"+{_DEFAULT_COUNTRY_CODE}{digits}"
    else:
        normalized = "+" + digits

    if not re.fullmatch(r"\+\d{8,15}", normalized):
        raise InvalidPhoneNumber(f"Could not normalize phone number: {raw!r}")
    return normalized


def find_customer_by_phone(session: Session, phone: str) -> Customer | None:
    normalized = normalize_phone(phone)
    return session.scalar(select(Customer).where(Customer.phone == normalized))


def resolve_or_create_customer(session: Session, phone: str, name: str | None = None) -> tuple[Customer, bool]:
    """Find a customer by phone, or create one if none exists.

    Returns (customer, created). If the customer already exists, their stored
    name is left untouched even if a different `name` is supplied here - a
    customer's own record is not silently overwritten by whatever they typed
    in one conversation.
    """
    normalized = normalize_phone(phone)
    existing = session.scalar(select(Customer).where(Customer.phone == normalized))
    if existing is not None:
        return existing, False

    if not name:
        raise ValueError("name is required to create a new customer")

    customer = Customer(phone=normalized, name=name)
    session.add(customer)
    session.flush()
    return customer, True
