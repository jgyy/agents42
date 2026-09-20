import datetime as dt_module
import uuid
from datetime import date, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import agents42.config as config_module
from agents42.api import app, get_calendar_client
from agents42.db import get_session
from agents42.integrations.google_calendar import CalendarError
from agents42.models import Base, Booking
from agents42.profiles.loader import load_business_profile
from agents42.scheduling.service import BusyPeriod

REPO_BUSINESSES_DIR = Path(__file__).resolve().parents[2] / "businesses"
# Read the actual demo profile rather than hardcoding its name/display copy,
# which is expected to be edited per-business without breaking tests.
DEMO_PROFILE = load_business_profile("demo-groomer", businesses_dir=REPO_BUSINESSES_DIR)


def next_weekday(base: date, weekday: int) -> date:
    """weekday: Monday=0 ... Sunday=6. Always returns a date after `base`."""
    days_ahead = (weekday - base.weekday()) % 7 or 7
    return base + timedelta(days=days_ahead)


FRIDAY = next_weekday(date.today(), 4)  # groomer.yaml has Friday hours


class FakeCalendarClient:
    """Stands in for GoogleCalendarClient in tests - CalendarClient is a
    Protocol precisely so this substitution is possible without touching a
    real Google account.
    """

    def __init__(self):
        self.busy: list[BusyPeriod] = []
        self.created_events: list[str] = []
        self.deleted_events: list[str] = []
        self.fail_get_busy = False
        self.fail_create = False
        self.fail_delete = False

    def get_busy_periods(self, calendar_id, start, end):
        if self.fail_get_busy:
            raise CalendarError("simulated calendar outage")
        # Like Google's freebusy API, only return periods that intersect the
        # requested window - a fake that ignores the window would hide bugs
        # where the caller asks for too narrow a range.
        self.last_query = (start, end)
        return [b for b in self.busy if b.start < end and start < b.end]

    def create_event(self, calendar_id, summary, start, end, description=""):
        if self.fail_create:
            raise CalendarError("simulated calendar outage")
        event_id = f"evt-{len(self.created_events) + 1}"
        self.created_events.append(event_id)
        return event_id

    def delete_event(self, calendar_id, event_id):
        if self.fail_delete:
            raise CalendarError("simulated calendar outage")
        self.deleted_events.append(event_id)


class FailingCommitSession:
    """Wraps a real Session but makes commit() raise, to exercise the
    partial-failure path (Calendar event created, DB write fails).
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def commit(self):
        raise RuntimeError("simulated DB failure")


@pytest.fixture
def test_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def fake_calendar():
    return FakeCalendarClient()


@pytest.fixture
def client(test_engine, fake_calendar, monkeypatch):
    monkeypatch.setattr(config_module.settings, "businesses_dir", REPO_BUSINESSES_DIR)

    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_calendar_client] = lambda: fake_calendar
    # No `with` block: skips the startup lifespan (which runs Postgres
    # migrations) - tests only ever touch the in-memory sqlite engine above.
    yield TestClient(app)
    app.dependency_overrides.clear()


def resolve_customer(client, phone="91234567", name="Sarah Tan"):
    response = client.post("/customers/resolve", json={"phone": phone, "name": name})
    assert response.status_code == 200, response.text
    return response.json()


def create_booking(client, customer_id, date_=FRIDAY, business_id="demo-groomer", service="full_grooming"):
    search = client.post(
        "/availability/search",
        json={"business_id": business_id, "service": service, "date": date_.isoformat()},
    )
    assert search.status_code == 200, search.text
    first_slot = search.json()["slots"][0]
    response = client.post(
        "/bookings",
        json={"business_id": business_id, "customer_id": customer_id, "service": service, "start": first_slot["start"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_unknown_phone_creates_customer(client):
    body = resolve_customer(client)
    assert body["found"] is True
    assert body["created"] is True
    assert body["name"] == "Sarah Tan"


def test_new_phone_without_name_returns_needs_name_not_422(client):
    response = client.post("/customers/resolve", json={"phone": "90001111"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["found"] is False
    assert body["needs_name"] is True
    assert body["id"] is None
    assert body["phone"] == "+6590001111"

    # Retrying with a name now succeeds and creates the customer.
    followup = resolve_customer(client, phone="90001111", name="Late Name")
    assert followup["found"] is True
    assert followup["created"] is True
    assert followup["name"] == "Late Name"


def test_returning_phone_is_recognised_without_duplicate(client):
    first = resolve_customer(client)
    second = resolve_customer(client, name="Someone Else")
    assert second["created"] is False
    assert second["id"] == first["id"]
    assert second["name"] == "Sarah Tan"


def test_availability_search_excludes_busy_period(client, fake_calendar):
    tz = ZoneInfo("Asia/Singapore")
    fake_calendar.busy = [
        BusyPeriod(
            start=dt_module.datetime.combine(FRIDAY, time(10, 0), tzinfo=tz),
            end=dt_module.datetime.combine(FRIDAY, time(12, 0), tzinfo=tz),
        )
    ]
    response = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    assert response.status_code == 200, response.text
    slots = response.json()["slots"]
    assert len(slots) > 0
    assert all("10:00" not in s["start"] and "11:00" not in s["start"] for s in slots)


def test_availability_search_respects_buffer_around_event_before_opening(client, fake_calendar):
    """An event that ends inside the turnaround buffer *before* opening time
    must still block the first slot. The free/busy query has to be widened by
    the buffer on both sides, or Calendar never reports that event at all.
    """
    tz = ZoneInfo("Asia/Singapore")
    opening = DEMO_PROFILE.opening_hours["friday"].open
    turnaround = DEMO_PROFILE.services["full_grooming"].turnaround_minutes
    day_open = dt_module.datetime.combine(FRIDAY, opening, tzinfo=tz)
    # Ends 15 minutes before opening - well inside the turnaround buffer.
    fake_calendar.busy = [BusyPeriod(start=day_open - timedelta(hours=1), end=day_open - timedelta(minutes=15))]

    response = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    assert response.status_code == 200, response.text
    starts = [dt_module.datetime.fromisoformat(s["start"]) for s in response.json()["slots"]]
    assert day_open not in starts, "first slot offered despite an event inside the turnaround buffer"
    assert day_open + timedelta(minutes=turnaround) in starts or not starts

    queried_start, queried_end = fake_calendar.last_query
    assert queried_start <= day_open - timedelta(minutes=turnaround)


def test_availability_search_respects_buffer_around_event_after_closing(client, fake_calendar):
    tz = ZoneInfo("Asia/Singapore")
    closing = DEMO_PROFILE.opening_hours["friday"].close
    duration = DEMO_PROFILE.services["full_grooming"].duration_minutes
    day_close = dt_module.datetime.combine(FRIDAY, closing, tzinfo=tz)
    # Starts 15 minutes after closing - inside the buffer of the last slot.
    fake_calendar.busy = [BusyPeriod(start=day_close + timedelta(minutes=15), end=day_close + timedelta(hours=1))]

    response = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    assert response.status_code == 200, response.text
    starts = [dt_module.datetime.fromisoformat(s["start"]) for s in response.json()["slots"]]
    assert day_close - timedelta(minutes=duration) not in starts


def test_booking_rejects_slot_inside_buffer_of_event_before_opening(client, fake_calendar):
    customer = resolve_customer(client)
    tz = ZoneInfo("Asia/Singapore")
    day_open = dt_module.datetime.combine(FRIDAY, DEMO_PROFILE.opening_hours["friday"].open, tzinfo=tz)
    fake_calendar.busy = [BusyPeriod(start=day_open - timedelta(hours=1), end=day_open - timedelta(minutes=15))]

    response = client.post(
        "/bookings",
        json={
            "business_id": "demo-groomer",
            "customer_id": customer["id"],
            "service": "full_grooming",
            "start": day_open.isoformat(),
        },
    )
    assert response.status_code == 409, response.text
    assert fake_calendar.created_events == []


def test_business_id_with_path_traversal_is_rejected(client):
    response = client.get("/businesses/..%2F..%2Fbusinesses%2Fdemo-groomer")
    assert response.status_code in (404, 422)
    response = client.post(
        "/availability/search",
        json={"business_id": "../businesses/demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    assert response.status_code in (404, 422), response.text


def test_availability_search_returns_empty_when_closed(client):
    sunday = next_weekday(FRIDAY, 6)
    response = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": sunday.isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["slots"] == []


def test_availability_search_502_when_calendar_down(client, fake_calendar):
    fake_calendar.fail_get_busy = True
    response = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    assert response.status_code == 502


def test_full_booking_flow_creates_calendar_event_and_db_row(client, fake_calendar):
    customer = resolve_customer(client)

    search = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    first_slot = search.json()["slots"][0]

    booking_response = client.post(
        "/bookings",
        json={
            "business_id": "demo-groomer",
            "customer_id": customer["id"],
            "service": "full_grooming",
            "start": first_slot["start"],
        },
    )
    assert booking_response.status_code == 201, booking_response.text
    body = booking_response.json()
    assert body["status"] == "confirmed"
    assert body["google_event_id"] == "evt-1"
    assert body["business_name"] == DEMO_PROFILE.name  # not just business_id - see AGENTS42.md
    assert fake_calendar.created_events == ["evt-1"]

    fetched = client.get(f"/bookings/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]
    assert fetched.json()["business_name"] == DEMO_PROFILE.name


def test_get_business_info(client):
    response = client.get("/businesses/demo-groomer")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == "demo-groomer"
    assert body["name"] == DEMO_PROFILE.name
    assert body["services"]["full_grooming"]["display_name"] == DEMO_PROFILE.services["full_grooming"].display_name
    assert body["services"]["full_grooming"]["duration_minutes"] == 120
    assert body["opening_hours"]["monday"] == {"open": "09:00", "close": "18:00"}
    assert "sunday" not in body["opening_hours"]


def test_get_business_info_unknown_business_404(client):
    response = client.get("/businesses/does-not-exist")
    assert response.status_code == 404


def test_get_business_info_includes_pricing_and_add_ons(client):
    response = client.get("/businesses/demo-groomer")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["services"]["full_grooming"]["price_from"] == "S$60"
    assert body["services"]["basic_grooming"]["price_from"] == "S$35"
    assert body["services"]["basic_grooming"]["duration_minutes"] == 120

    assert body["add_ons"]["bath"] == {"display_name": "Bath", "price_from": "S$25"}
    assert body["add_ons"]["ayurveda_herb_spa"]["price_from"] == "S$35"

    assert "SKC" in body["about"]
    assert "advised" in body["pricing_note"]


def test_basic_grooming_is_actually_bookable(client, fake_calendar):
    customer = resolve_customer(client)
    search = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "basic_grooming", "date": FRIDAY.isoformat()},
    )
    assert search.status_code == 200, search.text
    first_slot = search.json()["slots"][0]

    booking = client.post(
        "/bookings",
        json={
            "business_id": "demo-groomer",
            "customer_id": customer["id"],
            "service": "basic_grooming",
            "start": first_slot["start"],
        },
    )
    assert booking.status_code == 201, booking.text
    assert booking.json()["service"] == "basic_grooming"


def test_add_on_is_not_independently_bookable(client):
    customer = resolve_customer(client)
    response = client.post(
        "/bookings",
        json={
            "business_id": "demo-groomer",
            "customer_id": customer["id"],
            "service": "bath",
            "start": f"{FRIDAY.isoformat()}T09:00:00+08:00",
        },
    )
    assert response.status_code == 422


def test_booking_rechecks_availability_and_rejects_now_busy_slot(client, fake_calendar):
    customer = resolve_customer(client)
    search = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    first_slot = search.json()["slots"][0]

    # Simulate another booking landing on this slot between the search and
    # the customer's selection - the recheck-before-booking guard must catch it.
    start_dt = dt_module.datetime.fromisoformat(first_slot["start"])
    end_dt = dt_module.datetime.fromisoformat(first_slot["end"])
    fake_calendar.busy = [BusyPeriod(start=start_dt, end=end_dt)]

    response = client.post(
        "/bookings",
        json={
            "business_id": "demo-groomer",
            "customer_id": customer["id"],
            "service": "full_grooming",
            "start": first_slot["start"],
        },
    )
    assert response.status_code == 409
    assert fake_calendar.created_events == []


def test_booking_rejects_start_not_on_slot_interval(client, fake_calendar):
    customer = resolve_customer(client)
    tz = ZoneInfo("Asia/Singapore")
    # groomer.yaml's slot_interval_minutes is 60, starting from 09:00 - 09:37
    # is free on the calendar but was never an offerable slot.
    misaligned_start = dt_module.datetime.combine(FRIDAY, time(9, 37), tzinfo=tz)

    response = client.post(
        "/bookings",
        json={
            "business_id": "demo-groomer",
            "customer_id": customer["id"],
            "service": "full_grooming",
            "start": misaligned_start.isoformat(),
        },
    )
    assert response.status_code == 409
    assert fake_calendar.created_events == []


def test_booking_rejects_past_start_time(client, fake_calendar):
    customer = resolve_customer(client)
    tz = ZoneInfo("Asia/Singapore")
    # A Friday that has already passed (70 days = 10 weeks before FRIDAY, so
    # still a Friday), on a valid slot-interval boundary - only its pastness
    # should cause the rejection.
    past_friday = FRIDAY - timedelta(days=70)
    past_start = dt_module.datetime.combine(past_friday, time(9, 0), tzinfo=tz)

    response = client.post(
        "/bookings",
        json={
            "business_id": "demo-groomer",
            "customer_id": customer["id"],
            "service": "full_grooming",
            "start": past_start.isoformat(),
        },
    )
    assert response.status_code == 409
    assert fake_calendar.created_events == []


def test_booking_not_confirmed_when_calendar_create_fails(client, fake_calendar):
    customer = resolve_customer(client)
    search = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    first_slot = search.json()["slots"][0]
    fake_calendar.fail_create = True

    response = client.post(
        "/bookings",
        json={
            "business_id": "demo-groomer",
            "customer_id": customer["id"],
            "service": "full_grooming",
            "start": first_slot["start"],
        },
    )
    assert response.status_code == 502


def test_orphaned_calendar_event_is_deleted_when_db_write_fails(client, fake_calendar, test_engine, monkeypatch):
    customer = resolve_customer(client)
    search = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    first_slot = search.json()["slots"][0]

    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def failing_get_session():
        session = FailingCommitSession(TestSession())
        try:
            yield session
        finally:
            session._inner.close()

    app.dependency_overrides[get_session] = failing_get_session

    response = client.post(
        "/bookings",
        json={
            "business_id": "demo-groomer",
            "customer_id": customer["id"],
            "service": "full_grooming",
            "start": first_slot["start"],
        },
    )

    assert response.status_code == 500
    assert fake_calendar.created_events == ["evt-1"]
    assert fake_calendar.deleted_events == ["evt-1"]  # rolled back, no phantom booking

    with TestSession() as verify_session:
        assert verify_session.query(Booking).count() == 0


# --- Rescheduling ------------------------------------------------------------


def _mark_busy_like_the_real_calendar_would(fake_calendar, booking):
    """FakeCalendarClient.create_event doesn't update .busy automatically -
    tests that need a just-created booking to actually look occupied on a
    later get_busy_periods call must say so explicitly.
    """
    fake_calendar.busy = [
        BusyPeriod(
            start=dt_module.datetime.fromisoformat(booking["start"]),
            end=dt_module.datetime.fromisoformat(booking["end"]),
        )
    ]


def test_list_customer_bookings_empty_for_new_customer(client):
    customer = resolve_customer(client)
    response = client.get(f"/customers/{customer['id']}/bookings", params={"business_id": "demo-groomer"})
    assert response.status_code == 200, response.text
    assert response.json()["bookings"] == []


def test_list_customer_bookings_returns_upcoming_confirmed(client, fake_calendar):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    response = client.get(f"/customers/{customer['id']}/bookings", params={"business_id": "demo-groomer"})
    assert response.status_code == 200, response.text
    bookings = response.json()["bookings"]
    assert len(bookings) == 1
    assert bookings[0]["id"] == booking["id"]
    assert bookings[0]["business_name"] == DEMO_PROFILE.name


def test_list_customer_bookings_excludes_a_different_businesss_booking(client, fake_calendar, test_engine):
    """The same customer_id can have bookings with more than one agents42
    business (same phone number everywhere - see resolve_customer.py). A
    WhatsApp session for demo-groomer must never see - and, via
    reschedule/cancel, never be able to modify - a booking that belongs to
    a different business, even for the exact same customer.
    """
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    with TestSession() as s:
        row = s.get(Booking, uuid.UUID(booking["id"]))
        row.business_id = "some-other-business"
        s.commit()

    response = client.get(f"/customers/{customer['id']}/bookings", params={"business_id": "demo-groomer"})
    assert response.status_code == 200, response.text
    assert response.json()["bookings"] == []


def test_list_customer_bookings_unknown_customer_404(client):
    response = client.get(
        "/customers/00000000-0000-0000-0000-000000000000/bookings", params={"business_id": "demo-groomer"}
    )
    assert response.status_code == 404


def _different_slot_start(client, booking):
    """A valid, available start time on the booking's own date that isn't
    the booking's own current start - for tests that need to actually
    exercise the reschedule-to-a-new-time path rather than the same-slot
    no-op short-circuit.
    """
    search = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    new_slot = search.json()["slots"][0]  # first slot after the original + its buffer
    assert new_slot["start"] != booking["start"]
    return new_slot["start"]


def test_reschedule_to_a_different_slot_succeeds(client, fake_calendar):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])
    _mark_busy_like_the_real_calendar_would(fake_calendar, booking)
    new_start = _different_slot_start(client, booking)

    response = client.post(
        f"/bookings/{booking['id']}/reschedule",
        json={"customer_id": customer["id"], "business_id": "demo-groomer", "new_start": new_start},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == booking["id"]  # same booking, updated in place
    assert body["start"] == new_start
    assert body["google_event_id"] == "evt-2"
    assert fake_calendar.created_events == ["evt-1", "evt-2"]
    assert fake_calendar.deleted_events == ["evt-1"]  # old event cleaned up


def test_reschedule_to_the_exact_same_slot_is_a_calendar_free_noop(client, fake_calendar):
    """The booking's own current slot must not count as a conflict against
    itself - without that exclusion this would incorrectly 409. This is
    handled as an explicit no-op short-circuit (not by filtering Calendar
    busy periods by matching time, which risked hiding a genuinely
    different event that happens to share the same start/end) - so no
    Calendar call happens at all.
    """
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])
    _mark_busy_like_the_real_calendar_would(fake_calendar, booking)

    response = client.post(
        f"/bookings/{booking['id']}/reschedule",
        json={"customer_id": customer["id"], "business_id": "demo-groomer", "new_start": booking["start"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["start"] == booking["start"]
    assert response.json()["google_event_id"] == "evt-1"  # unchanged - nothing was moved
    assert fake_calendar.created_events == ["evt-1"]  # no new event created
    assert fake_calendar.deleted_events == []


def test_reschedule_rejects_a_genuinely_occupied_slot(client, fake_calendar):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])
    _mark_busy_like_the_real_calendar_would(fake_calendar, booking)

    other_start = dt_module.datetime.fromisoformat(booking["start"]) + timedelta(hours=4)
    other_end = other_start + timedelta(hours=2)
    fake_calendar.busy.append(BusyPeriod(start=other_start, end=other_end))

    response = client.post(
        f"/bookings/{booking['id']}/reschedule",
        json={"customer_id": customer["id"], "business_id": "demo-groomer", "new_start": other_start.isoformat()},
    )
    assert response.status_code == 409
    assert fake_calendar.created_events == ["evt-1"]  # no new event created


def test_reschedule_wrong_customer_id_404(client, fake_calendar):
    customer = resolve_customer(client)
    other_customer = resolve_customer(client, phone="90009999", name="Someone Else")
    booking = create_booking(client, customer["id"])
    _mark_busy_like_the_real_calendar_would(fake_calendar, booking)

    response = client.post(
        f"/bookings/{booking['id']}/reschedule",
        json={"customer_id": other_customer["id"], "business_id": "demo-groomer", "new_start": booking["start"]},
    )
    assert response.status_code == 404


def test_reschedule_wrong_business_id_404(client, fake_calendar):
    """A booking made with one business must not be reschedulable from a
    WhatsApp session bound to a different business, even for the same
    customer - the business is fixed per session, never customer-supplied.
    """
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])
    _mark_busy_like_the_real_calendar_would(fake_calendar, booking)

    response = client.post(
        f"/bookings/{booking['id']}/reschedule",
        json={"customer_id": customer["id"], "business_id": "some-other-business", "new_start": booking["start"]},
    )
    assert response.status_code == 404


def test_reschedule_unknown_booking_404(client):
    customer = resolve_customer(client)
    response = client.post(
        "/bookings/00000000-0000-0000-0000-000000000000/reschedule",
        json={
            "customer_id": customer["id"],
            "business_id": "demo-groomer",
            "new_start": FRIDAY.isoformat() + "T09:00:00+08:00",
        },
    )
    assert response.status_code == 404


def test_reschedule_not_confirmed_when_calendar_down(client, fake_calendar):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])
    _mark_busy_like_the_real_calendar_would(fake_calendar, booking)
    new_start = _different_slot_start(client, booking)
    fake_calendar.fail_get_busy = True

    response = client.post(
        f"/bookings/{booking['id']}/reschedule",
        json={"customer_id": customer["id"], "business_id": "demo-groomer", "new_start": new_start},
    )
    assert response.status_code == 502


def test_reschedule_not_confirmed_when_new_event_create_fails(client, fake_calendar):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])
    _mark_busy_like_the_real_calendar_would(fake_calendar, booking)
    new_start = _different_slot_start(client, booking)
    fake_calendar.fail_create = True

    response = client.post(
        f"/bookings/{booking['id']}/reschedule",
        json={"customer_id": customer["id"], "business_id": "demo-groomer", "new_start": new_start},
    )
    assert response.status_code == 502
    # Original booking/event untouched - nothing was deleted or changed.
    assert fake_calendar.created_events == ["evt-1"]
    assert fake_calendar.deleted_events == []
    unchanged = client.get(f"/bookings/{booking['id']}")
    assert unchanged.json()["start"] == booking["start"]
    assert unchanged.json()["google_event_id"] == "evt-1"


def test_reschedule_rejects_a_non_confirmed_booking(client, fake_calendar, test_engine):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])
    _mark_busy_like_the_real_calendar_would(fake_calendar, booking)

    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    with TestSession() as s:
        row = s.get(Booking, uuid.UUID(booking["id"]))
        row.status = "cancelled"
        s.commit()

    response = client.post(
        f"/bookings/{booking['id']}/reschedule",
        json={"customer_id": customer["id"], "business_id": "demo-groomer", "new_start": booking["start"]},
    )
    assert response.status_code == 422
    assert fake_calendar.created_events == ["evt-1"]  # only from the original booking, no reschedule attempt


def test_orphaned_new_event_is_deleted_when_reschedule_db_write_fails(client, fake_calendar, test_engine):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])
    _mark_busy_like_the_real_calendar_would(fake_calendar, booking)
    new_start = _different_slot_start(client, booking)

    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def failing_get_session():
        session = FailingCommitSession(TestSession())
        try:
            yield session
        finally:
            session._inner.close()

    app.dependency_overrides[get_session] = failing_get_session

    response = client.post(
        f"/bookings/{booking['id']}/reschedule",
        json={"customer_id": customer["id"], "business_id": "demo-groomer", "new_start": new_start},
    )

    assert response.status_code == 500
    assert fake_calendar.created_events == ["evt-1", "evt-2"]
    assert fake_calendar.deleted_events == ["evt-2"]  # only the orphaned new event, not the original

    # Restore a working session to verify the original booking is untouched.
    app.dependency_overrides[get_session] = lambda: iter([TestSession()])
    with TestSession() as verify_session:
        row = verify_session.get(Booking, uuid.UUID(booking["id"]))
        assert row.google_event_id == "evt-1"


# --- Cancellation -------------------------------------------------------------


class FailOnceThenSucceedCommitSession:
    """Wraps a real Session but makes only the *first* commit() raise,
    succeeding on any later commit - for testing cancellation's
    delete-then-recreate compensation path, where a second, separate commit
    (persisting the recreated event's id) is expected to succeed after the
    first one (marking the booking cancelled) failed.
    """

    def __init__(self, inner):
        self._inner = inner
        self._commit_count = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def commit(self):
        self._commit_count += 1
        if self._commit_count == 1:
            raise RuntimeError("simulated DB failure")
        self._inner.commit()


def test_cancel_succeeds_and_removes_calendar_event(client, fake_calendar):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    response = client.post(
        f"/bookings/{booking['id']}/cancel",
        json={"customer_id": customer["id"], "business_id": "demo-groomer"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert response.json()["id"] == booking["id"]
    assert fake_calendar.deleted_events == ["evt-1"]


def test_cancelled_booking_no_longer_appears_in_upcoming_list(client, fake_calendar):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    client.post(
        f"/bookings/{booking['id']}/cancel", json={"customer_id": customer["id"], "business_id": "demo-groomer"}
    )

    upcoming = client.get(f"/customers/{customer['id']}/bookings", params={"business_id": "demo-groomer"})
    assert upcoming.json()["bookings"] == []


def test_cancel_wrong_customer_id_404(client):
    customer = resolve_customer(client)
    other_customer = resolve_customer(client, phone="90009999", name="Someone Else")
    booking = create_booking(client, customer["id"])

    response = client.post(
        f"/bookings/{booking['id']}/cancel",
        json={"customer_id": other_customer["id"], "business_id": "demo-groomer"},
    )
    assert response.status_code == 404


def test_cancel_wrong_business_id_404(client, fake_calendar):
    """Same boundary as reschedule: a booking made with one business must
    not be cancellable from a WhatsApp session bound to a different
    business, even for the same customer.
    """
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    response = client.post(
        f"/bookings/{booking['id']}/cancel",
        json={"customer_id": customer["id"], "business_id": "some-other-business"},
    )
    assert response.status_code == 404
    assert fake_calendar.deleted_events == []  # never touched - rejected before any Calendar call


def test_cancel_unknown_booking_404(client):
    customer = resolve_customer(client)
    response = client.post(
        "/bookings/00000000-0000-0000-0000-000000000000/cancel",
        json={"customer_id": customer["id"], "business_id": "demo-groomer"},
    )
    assert response.status_code == 404


def test_cancel_rejects_an_already_cancelled_booking(client, test_engine):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    with TestSession() as s:
        row = s.get(Booking, uuid.UUID(booking["id"]))
        row.status = "cancelled"
        s.commit()

    response = client.post(
        f"/bookings/{booking['id']}/cancel",
        json={"customer_id": customer["id"], "business_id": "demo-groomer"},
    )
    assert response.status_code == 422


def test_cancel_fails_closed_when_calendar_delete_fails(client, fake_calendar):
    """Calendar free/busy is what availability search actually checks, so a
    cancellation must not report success while the Calendar event still
    exists - that would silently make the slot permanently unavailable.
    Nothing has changed yet at this point, so fail closed with a 502 rather
    than marking the DB cancelled anyway.
    """
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])
    fake_calendar.fail_delete = True

    response = client.post(
        f"/bookings/{booking['id']}/cancel",
        json={"customer_id": customer["id"], "business_id": "demo-groomer"},
    )
    assert response.status_code == 502

    unchanged = client.get(f"/bookings/{booking['id']}")
    assert unchanged.json()["status"] == "confirmed"


def test_cancel_db_write_fails_recreates_calendar_event(client, fake_calendar, test_engine):
    """Calendar delete succeeds, but the DB commit marking the booking
    cancelled then fails - the compensation must recreate an equivalent
    Calendar event (not just log and move on), so the booking stays
    genuinely confirmed - both in the DB and on the Calendar - rather than
    confirmed in the DB with no Calendar hold on its time at all.
    """
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def failing_once_get_session():
        session = FailOnceThenSucceedCommitSession(TestSession())
        try:
            yield session
        finally:
            session._inner.close()

    app.dependency_overrides[get_session] = failing_once_get_session

    response = client.post(
        f"/bookings/{booking['id']}/cancel",
        json={"customer_id": customer["id"], "business_id": "demo-groomer"},
    )
    assert response.status_code == 500
    assert fake_calendar.deleted_events == ["evt-1"]  # the original event was deleted...
    assert fake_calendar.created_events == ["evt-1", "evt-2"]  # ...then recreated

    app.dependency_overrides[get_session] = lambda: iter([TestSession()])
    with TestSession() as verify_session:
        row = verify_session.get(Booking, uuid.UUID(booking["id"]))
        assert row.status == "confirmed"  # recovered, not left cancelled
        assert row.google_event_id == "evt-2"  # points at the recreated event, not the deleted one


def test_cancel_db_write_fails_and_calendar_recreate_also_fails_does_not_crash(client, fake_calendar, test_engine):
    """One of two distinct double-failure cases: Calendar deleted, DB
    commit fails, and the *recreate call itself* also fails - no
    replacement event exists at all. Can't be fully healed automatically,
    but must still fail cleanly with a 500, not raise an unhandled error.
    """
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def failing_get_session():
        session = FailingCommitSession(TestSession())  # always fails
        try:
            yield session
        finally:
            session._inner.close()

    app.dependency_overrides[get_session] = failing_get_session
    fake_calendar.fail_create = True  # the recreate attempt fails too

    response = client.post(
        f"/bookings/{booking['id']}/cancel",
        json={"customer_id": customer["id"], "business_id": "demo-groomer"},
    )
    assert response.status_code == 500
    assert fake_calendar.created_events == ["evt-1"]  # no replacement was created


def test_cancel_db_write_fails_and_recreate_db_persist_also_fails_does_not_crash(client, fake_calendar, test_engine):
    """The other distinct double-failure case: the replacement event
    genuinely gets created on Calendar, but persisting its id back to the
    booking also fails - a different manual-cleanup situation from the
    "no replacement exists" case above (here a live, untracked event
    exists, and the DB still points at the deleted original).
    """
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def failing_get_session():
        session = FailingCommitSession(TestSession())  # every commit() fails
        try:
            yield session
        finally:
            session._inner.close()

    app.dependency_overrides[get_session] = failing_get_session
    # fake_calendar.fail_create stays False - the recreate call itself succeeds this time.

    response = client.post(
        f"/bookings/{booking['id']}/cancel",
        json={"customer_id": customer["id"], "business_id": "demo-groomer"},
    )
    assert response.status_code == 500
    assert fake_calendar.deleted_events == ["evt-1"]
    assert fake_calendar.created_events == ["evt-1", "evt-2"]  # the replacement WAS created on Calendar

    app.dependency_overrides[get_session] = lambda: iter([TestSession()])
    with TestSession() as verify_session:
        row = verify_session.get(Booking, uuid.UUID(booking["id"]))
        assert row.status == "confirmed"  # DB never got the "cancelled" write
        assert row.google_event_id == "evt-1"  # still points at the deleted original, not the new "evt-2"
