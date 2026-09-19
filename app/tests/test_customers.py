import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agents42.customers.service import (
    InvalidPhoneNumber,
    normalize_phone,
    resolve_or_create_customer,
)
from agents42.models import Base


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("91234567", "+6591234567"),
        ("+65 9123 4567", "+6591234567"),
        ("+6591234567", "+6591234567"),
        ("0065-9123-4567", "+6591234567"),
        (" 9123 4567 ", "+6591234567"),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_normalize_phone_rejects_empty():
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone("")


def test_unknown_phone_creates_customer(session):
    resolution = resolve_or_create_customer(session, "91234567", name="Sarah Tan")
    session.commit()

    assert resolution.created is True
    assert resolution.needs_name is False
    assert resolution.customer.name == "Sarah Tan"
    assert resolution.customer.phone == "+6591234567"


def test_existing_phone_does_not_duplicate(session):
    first = resolve_or_create_customer(session, "91234567", name="Sarah Tan")
    session.commit()

    second = resolve_or_create_customer(session, "+65 9123 4567", name="Someone Else")
    session.commit()

    assert first.created is True
    assert second.created is False
    assert second.customer.id == first.customer.id
    # existing record is not silently overwritten by a different name on lookup
    assert second.customer.name == "Sarah Tan"


def test_new_phone_without_name_needs_name_instead_of_raising(session):
    resolution = resolve_or_create_customer(session, "91234568", name=None)

    assert resolution.needs_name is True
    assert resolution.customer is None
    assert resolution.created is False
    assert resolution.phone == "+6591234568"
