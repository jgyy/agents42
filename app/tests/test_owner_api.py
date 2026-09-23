import datetime as dt_module
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import agents42.config as config_module
from agents42.api import app as customer_app
from agents42.api import get_calendar_client, get_email_notifier
from agents42.db import get_session
from agents42.integrations.email_notifier import EmailError
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


def test_resolve_escalation_marks_resolved_and_moves_to_activity(client, owner_client, test_engine):
    response = client.post("/escalations", json={"business_id": "demo-groomer", "reason": "complaint"})
    escalation_id = response.json()["id"]

    dashboard_before = owner_client.get("/", auth=AUTH)
    assert f"/escalations/{escalation_id}/resolve" in dashboard_before.text  # open - resolvable

    resolve = owner_client.post(f"/escalations/{escalation_id}/resolve", auth=AUTH)
    assert resolve.status_code == 303

    dashboard_after = owner_client.get("/", auth=AUTH)
    # No longer actionable in Attention...
    assert f"/escalations/{escalation_id}/resolve" not in dashboard_after.text
    # ...but still visible as history under Activity, not vanished entirely.
    assert "complaint" in dashboard_after.text

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


# --- Escalation owner-notification email ---------------------------------


class FakeEmailNotifier:
    """Stands in for SmtpEmailNotifier in tests - EmailNotifier is a
    Protocol precisely so this substitution is possible without touching a
    real SMTP account.
    """

    def __init__(self):
        self.sent = []
        self.fail = False

    def send(self, to_addr, subject, body):
        if self.fail:
            raise EmailError("simulated SMTP failure")
        self.sent.append({"to": to_addr, "subject": subject, "body": body})


@pytest.fixture
def fake_email_notifier(monkeypatch):
    monkeypatch.setattr(config_module.settings, "owner_notification_email", "owner@example.com")
    notifier = FakeEmailNotifier()
    customer_app.dependency_overrides[get_email_notifier] = lambda: notifier
    yield notifier
    customer_app.dependency_overrides.pop(get_email_notifier, None)


def test_escalation_sends_owner_email_when_configured(client, fake_email_notifier):
    customer = resolve_customer(client, phone="91234567", name="Sarah Tan")
    booking = create_booking(client, customer["id"])

    response = client.post(
        "/escalations",
        json={
            "business_id": "demo-groomer",
            "customer_id": customer["id"],
            "booking_id": booking["id"],
            "reason": "Refund request",
            "detail": "Customer says grooming was not satisfactory.",
        },
    )
    assert response.status_code == 201, response.text

    assert len(fake_email_notifier.sent) == 1
    email = fake_email_notifier.sent[0]
    assert email["to"] == "owner@example.com"
    assert "needs attention" in email["subject"]
    assert "Sarah Tan" in email["body"]
    assert "+6591234567" not in email["body"]  # full number never sent over this channel
    assert "4567" in email["body"]  # last 4 digits still shown, for a quick match against the dashboard
    assert "Refund request" in email["body"]
    assert "not satisfactory" in email["body"]
    assert "full_grooming" in email["body"]


def test_escalation_skips_email_when_not_configured(client):
    # No fake_email_notifier fixture here - owner_notification_email/smtp_host
    # are unset by default, so get_email_notifier() returns None.
    response = client.post("/escalations", json={"business_id": "demo-groomer", "reason": "x"})
    assert response.status_code == 201, response.text


def test_escalation_creation_succeeds_even_when_email_fails(client, fake_email_notifier):
    fake_email_notifier.fail = True
    response = client.post("/escalations", json={"business_id": "demo-groomer", "reason": "x"})
    assert response.status_code == 201, response.text  # the escalation itself must not be affected


def test_mask_phone_for_email():
    from agents42.api import _mask_phone_for_email

    assert _mask_phone_for_email("+6591234567") == "+65****4567"
    assert _mask_phone_for_email("123") == "123"  # too short to usefully mask - returned as-is


# --- Customers --------------------------------------------------------------


def test_customer_appears_in_customers_section_with_booking_count(client, owner_client):
    customer = resolve_customer(client)
    create_booking(client, customer["id"])

    response = owner_client.get("/", auth=AUTH)
    assert customer["name"] in response.text
    assert "1 booking" in response.text


def _customers_section(html: str) -> str:
    """Bookings for other customers legitimately appear elsewhere on the
    page (Today/Upcoming aren't filtered by customer_q, only the Customers
    section is) - scope search-result assertions to just that section
    rather than the whole page.
    """
    return html.split('id="customers"')[1].split('id="business"')[0]


def test_customer_search_filters_by_name_and_phone(client, owner_client):
    sarah = resolve_customer(client, phone="91234567", name="Sarah Tan")
    create_booking(client, sarah["id"])
    john = resolve_customer(client, phone="90009999", name="John Lim")
    create_booking(client, john["id"], date_=FRIDAY, business_id="demo-groomer")

    by_name = _customers_section(owner_client.get("/", params={"customer_q": "Sarah"}, auth=AUTH).text)
    assert "Sarah Tan" in by_name
    assert "John Lim" not in by_name

    by_phone = _customers_section(owner_client.get("/", params={"customer_q": "90009999"}, auth=AUTH).text)
    assert "John Lim" in by_phone
    assert "Sarah Tan" not in by_phone


def test_customer_list_excludes_customer_with_only_a_different_businesss_booking(
    client, owner_client, test_engine
):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    with _session_for(test_engine)() as s:
        row = s.get(Booking, uuid.UUID(booking["id"]))
        row.business_id = "some-other-business"
        s.commit()

    response = owner_client.get("/", auth=AUTH)
    assert customer["name"] not in response.text


def test_customer_detail_shows_booking_history_with_display_status(client, owner_client, fake_calendar):
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])
    client.post(f"/bookings/{booking['id']}/cancel", json={"customer_id": customer["id"], "business_id": "demo-groomer"})

    response = owner_client.get(f"/customers/{customer['id']}", auth=AUTH)
    assert response.status_code == 200
    assert customer["name"] in response.text
    assert customer["phone"] in response.text
    assert "cancelled" in response.text


def test_customer_detail_unknown_customer_404(owner_client):
    response = owner_client.get("/customers/00000000-0000-0000-0000-000000000000", auth=AUTH)
    assert response.status_code == 404


def test_customer_detail_business_scoping_404(client, owner_client, test_engine):
    """A customer who only has bookings with a *different* business must not
    be viewable through this business's dashboard, even though the Customer
    row itself exists (same reasoning as the customer list exclusion above).
    """
    customer = resolve_customer(client)
    booking = create_booking(client, customer["id"])

    with _session_for(test_engine)() as s:
        row = s.get(Booking, uuid.UUID(booking["id"]))
        row.business_id = "some-other-business"
        s.commit()

    response = owner_client.get(f"/customers/{customer['id']}", auth=AUTH)
    assert response.status_code == 404


# --- CSRF: cross-site POSTs must be rejected ---------------------------------
#
# Browsers cache Basic Auth credentials and attach them to *any* request to
# this origin, including a form POST from a hostile page. Without a
# same-origin check, an owner who is logged in and visits such a page could
# have bookings cancelled or slots blocked without knowing.


def test_cross_site_post_rejected_by_sec_fetch_site(owner_client):
    response = owner_client.post("/block-time", data={"start": "x", "end": "y"}, auth=AUTH, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403


def test_same_site_subdomain_post_rejected(owner_client):
    response = owner_client.post("/block-time", data={"start": "x", "end": "y"}, auth=AUTH, headers={"Sec-Fetch-Site": "same-site"})
    assert response.status_code == 403


def test_same_origin_post_allowed(owner_client, fake_calendar):
    start, end = "2026-09-22T12:00:00+08:00", "2026-09-22T13:00:00+08:00"
    response = owner_client.post(
        "/block-time", data={"start": start, "end": end}, auth=AUTH, headers={"Sec-Fetch-Site": "same-origin"}
    )
    assert response.status_code in (200, 303)
    assert response.status_code != 403


def test_mismatched_origin_rejected_without_sec_fetch_site(owner_client):
    response = owner_client.post("/block-time", data={"start": "x", "end": "y"}, auth=AUTH, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_matching_origin_allowed_without_sec_fetch_site(owner_client):
    host = owner_client.base_url.host
    response = owner_client.post(
        "/block-time", data={"start": "x", "end": "y"}, auth=AUTH, headers={"Origin": f"http://{host}"}
    )
    assert response.status_code != 403


def test_cross_site_get_still_allowed(owner_client):
    # Navigations to the dashboard from a link elsewhere are fine; only
    # state-changing methods need the same-origin guard.
    response = owner_client.get("/", auth=AUTH, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 200


def test_null_origin_rejected(owner_client):
    response = owner_client.post("/block-time", data={"start": "x", "end": "y"}, auth=AUTH, headers={"Origin": "null"})
    assert response.status_code == 403
