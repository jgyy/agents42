import datetime as dt_module
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import agents42.config as config_module
from agents42.api import app as customer_app
from agents42.api import get_calendar_client
from agents42.db import get_session
from agents42.models import BlockedSlot, Booking, Escalation
from agents42.owner_api import app as owner_app
from agents42.scheduling.service import BusyPeriod
from tests.test_api import (
    FRIDAY,
    REPO_BUSINESSES_DIR,
    FailingCommitSession,
    FakeCalendarClient,
    create_booking,
    resolve_customer,
    test_engine,  # noqa: F401 - reused as a pytest fixture
)

AUTH = ("owner", "test-password")


@pytest.fixture
def fake_calendar():
    return FakeCalendarClient()


@pytest.fixture
def client(test_engine, fake_calendar, monkeypatch):
    """The customer-facing app, wired to the same engine/calendar the owner
    client below uses - several tests here check cross-app effects (owner
    blocks time -> customer search excludes it; agent escalates via this
    app -> shows up on the owner dashboard).
    """
    monkeypatch.setattr(config_module.settings, "businesses_dir", REPO_BUSINESSES_DIR)

    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    customer_app.dependency_overrides[get_session] = override_get_session
    customer_app.dependency_overrides[get_calendar_client] = lambda: fake_calendar
    yield TestClient(customer_app)
    customer_app.dependency_overrides.clear()


@pytest.fixture
def owner_client(test_engine, fake_calendar, monkeypatch):
    monkeypatch.setattr(config_module.settings, "businesses_dir", REPO_BUSINESSES_DIR)
    monkeypatch.setattr(config_module.settings, "owner_dashboard_password", "test-password")
    monkeypatch.setattr(config_module.settings, "owner_dashboard_business_id", "demo-groomer")

    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    owner_app.dependency_overrides[get_session] = override_get_session
    owner_app.dependency_overrides[get_calendar_client] = lambda: fake_calendar
    # No `with` block: skips the lifespan (which would run Postgres
    # migrations and re-validate settings at import time) - same reasoning
    # as test_api.py's client fixture.
    yield TestClient(owner_app, follow_redirects=False)
    owner_app.dependency_overrides.clear()


def _session_for(test_engine):
    return sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)


# --- Auth ----------------------------------------------------------------


def test_requires_auth_401(owner_client):
    response = owner_client.get("/")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_wrong_password_401(owner_client):
    response = owner_client.get("/", auth=("owner", "wrong-password"))
    assert response.status_code == 401


def test_empty_configured_password_refuses_auth(owner_client, monkeypatch):
    monkeypatch.setattr(config_module.settings, "owner_dashboard_password", "")
    response = owner_client.get("/", auth=AUTH)
    assert response.status_code == 401


def test_health_does_not_require_auth(owner_client):
    response = owner_client.get("/health")
    assert response.status_code == 200


# --- Dashboard rendering ---------------------------------------------------


def test_dashboard_renders_with_today_and_upcoming_bookings(client, owner_client, fake_calendar):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    response = owner_client.get("/", auth=AUTH)
    assert response.status_code == 200
    assert customer["name"] in response.text
    assert customer["phone"] in response.text
    assert booking["id"] in response.text


def test_dashboard_business_scoping(client, owner_client, fake_calendar, test_engine):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    with _session_for(test_engine)() as s:
        row = s.get(Booking, uuid.UUID(booking["id"]))
        row.business_id = "some-other-business"
        s.commit()

    response = owner_client.get("/", auth=AUTH)
    assert booking["id"] not in response.text

    reschedule = owner_client.post(
        f"/bookings/{booking['id']}/reschedule", data={"new_start": booking["start"]}, auth=AUTH
    )
    assert reschedule.status_code == 404

    cancel = owner_client.post(f"/bookings/{booking['id']}/cancel", auth=AUTH)
    assert cancel.status_code == 404


# --- Owner reschedule/cancel bypass customer_id, still enforce business_id --------


def test_owner_reschedule_bypasses_customer_id_but_enforces_business_id(client, owner_client, fake_calendar):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    search = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    new_slot = search.json()["slots"][0]
    fake_calendar.busy = [
        BusyPeriod(
            start=dt_module.datetime.fromisoformat(booking["start"]),
            end=dt_module.datetime.fromisoformat(booking["end"]),
        )
    ]

    response = owner_client.post(
        f"/bookings/{booking['id']}/reschedule", data={"new_start": new_slot["start"]}, auth=AUTH
    )
    assert response.status_code == 303
    unchanged = client.get(f"/bookings/{booking['id']}")
    assert unchanged.json()["start"] == new_slot["start"]


def test_owner_cancel_bypasses_customer_id_but_enforces_business_id(client, owner_client, fake_calendar):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    response = owner_client.post(f"/bookings/{booking['id']}/cancel", auth=AUTH)
    assert response.status_code == 303
    result = client.get(f"/bookings/{booking['id']}")
    assert result.json()["status"] == "cancelled"
    assert fake_calendar.deleted_events == ["evt-1"]


def test_owner_reschedule_slot_conflict_flashes_error_and_leaves_original_booking(client, owner_client, fake_calendar):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    other_start = dt_module.datetime.fromisoformat(booking["start"]) + timedelta(hours=4)
    other_end = other_start + timedelta(hours=2)
    fake_calendar.busy.append(BusyPeriod(start=other_start, end=other_end))

    response = owner_client.post(
        f"/bookings/{booking['id']}/reschedule", data={"new_start": other_start.isoformat()}, auth=AUTH
    )
    assert response.status_code == 303
    assert "location" in response.headers and "level=error" in response.headers["location"]

    unchanged = client.get(f"/bookings/{booking['id']}")
    assert unchanged.json()["start"] == booking["start"]


# --- Block time -------------------------------------------------------------


def test_block_time_creates_calendar_event_and_db_row(owner_client, fake_calendar, test_engine):
    start = f"{FRIDAY.isoformat()}T14:00:00"
    end = f"{FRIDAY.isoformat()}T15:00:00"
    response = owner_client.post("/block-time", data={"start": start, "end": end, "reason": "Lunch"}, auth=AUTH)
    assert response.status_code == 303
    assert fake_calendar.created_events == ["evt-1"]

    with _session_for(test_engine)() as s:
        rows = s.query(BlockedSlot).all()
        assert len(rows) == 1
        assert rows[0].reason == "Lunch"
        assert rows[0].google_event_id == "evt-1"


def test_block_time_db_failure_rolls_back_calendar_event(owner_client, fake_calendar, test_engine):
    TestSession = _session_for(test_engine)

    def failing_get_session():
        session = FailingCommitSession(TestSession())
        try:
            yield session
        finally:
            session._inner.close()

    owner_app.dependency_overrides[get_session] = failing_get_session

    start = f"{FRIDAY.isoformat()}T14:00:00"
    end = f"{FRIDAY.isoformat()}T15:00:00"
    response = owner_client.post("/block-time", data={"start": start, "end": end}, auth=AUTH)
    assert response.status_code == 303
    assert "level=error" in response.headers["location"]
    assert fake_calendar.created_events == ["evt-1"]
    assert fake_calendar.deleted_events == ["evt-1"]  # rolled back

    owner_app.dependency_overrides[get_session] = lambda: iter([TestSession()])
    with TestSession() as s:
        assert s.query(BlockedSlot).count() == 0


def test_block_time_then_search_availability_excludes_it(client, owner_client, fake_calendar):
    search_before = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    first_slot_before = search_before.json()["slots"][0]

    owner_client.post(
        "/block-time",
        data={"start": first_slot_before["start"], "end": first_slot_before["end"], "reason": "Blocked"},
        auth=AUTH,
    )
    # FakeCalendarClient.create_event doesn't update .busy automatically -
    # same reasoning as test_api.py's _mark_busy_like_the_real_calendar_would.
    fake_calendar.busy.append(
        BusyPeriod(
            start=dt_module.datetime.fromisoformat(first_slot_before["start"]),
            end=dt_module.datetime.fromisoformat(first_slot_before["end"]),
        )
    )

    search_after = client.post(
        "/availability/search",
        json={"business_id": "demo-groomer", "service": "full_grooming", "date": FRIDAY.isoformat()},
    )
    starts_after = [s["start"] for s in search_after.json()["slots"]]
    assert first_slot_before["start"] not in starts_after


def test_unblock_deletes_calendar_event_and_db_row(owner_client, fake_calendar, test_engine):
    start = f"{FRIDAY.isoformat()}T14:00:00"
    end = f"{FRIDAY.isoformat()}T15:00:00"
    owner_client.post("/block-time", data={"start": start, "end": end}, auth=AUTH)

    with _session_for(test_engine)() as s:
        blocked_id = str(s.query(BlockedSlot).one().id)

    response = owner_client.post(f"/blocked-slots/{blocked_id}/unblock", auth=AUTH)
    assert response.status_code == 303
    assert fake_calendar.deleted_events == ["evt-1"]

    with _session_for(test_engine)() as s:
        assert s.query(BlockedSlot).count() == 0


def test_unblock_calendar_failure_leaves_db_row_untouched(owner_client, fake_calendar, test_engine):
    start = f"{FRIDAY.isoformat()}T14:00:00"
    end = f"{FRIDAY.isoformat()}T15:00:00"
    owner_client.post("/block-time", data={"start": start, "end": end}, auth=AUTH)

    with _session_for(test_engine)() as s:
        blocked_id = str(s.query(BlockedSlot).one().id)

    fake_calendar.fail_delete = True
    response = owner_client.post(f"/blocked-slots/{blocked_id}/unblock", auth=AUTH)
    assert response.status_code == 303
    assert "level=error" in response.headers["location"]

    with _session_for(test_engine)() as s:
        assert s.query(BlockedSlot).count() == 1


# --- Escalations --------------------------------------------------------------


def test_escalation_created_via_customer_api_appears_in_dashboard(client, owner_client):
    customer = resolve_customer(client)
    response = client.post(
        "/escalations",
        json={"business_id": "demo-groomer", "customer_id": customer["id"], "reason": "asked for a discount"},
    )
    assert response.status_code == 201, response.text

    dashboard = owner_client.get("/", auth=AUTH)
    assert "asked for a discount" in dashboard.text
    assert customer["name"] in dashboard.text


def test_resolve_escalation_marks_resolved_and_disappears_from_open_list(client, owner_client, test_engine):
    response = client.post("/escalations", json={"business_id": "demo-groomer", "reason": "complaint"})
    escalation_id = response.json()["id"]

    resolve = owner_client.post(f"/escalations/{escalation_id}/resolve", auth=AUTH)
    assert resolve.status_code == 303

    dashboard = owner_client.get("/", auth=AUTH)
    assert "complaint" not in dashboard.text

    with _session_for(test_engine)() as s:
        assert s.get(Escalation, uuid.UUID(escalation_id)).status == "resolved"


def test_escalation_unknown_business_404(client):
    response = client.post("/escalations", json={"business_id": "no-such-business", "reason": "x"})
    assert response.status_code == 404


def test_escalation_unknown_customer_404(client):
    response = client.post(
        "/escalations",
        json={
            "business_id": "demo-groomer",
            "customer_id": "00000000-0000-0000-0000-000000000000",
            "reason": "x",
        },
    )
    assert response.status_code == 404


def test_escalation_unknown_booking_404(client):
    response = client.post(
        "/escalations",
        json={
            "business_id": "demo-groomer",
            "booking_id": "00000000-0000-0000-0000-000000000000",
            "reason": "x",
        },
    )
    assert response.status_code == 404
