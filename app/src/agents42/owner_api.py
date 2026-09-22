"""Owner-facing HTTP boundary: server-rendered dashboard + owner-authority
booking/blocking actions. Single shared-password Basic Auth (see
require_owner_auth) - this process is the one deliberate exception to the
customer-facing app's 127.0.0.1-only hardening (see DEVELOPMENT.md "Owner
dashboard"). Reuses api.py's models/session/calendar-client/scheduling logic
directly rather than duplicating it - notably the reschedule/cancel
Calendar+DB compensation logic, which is non-trivial and must stay identical
for customer- and owner-triggered changes alike.
"""

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import date as date_type
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents42.api import (
    _as_aware,
    _cancel_booking_core,
    _compute_available_slots,
    _load_profile,
    _parse_uuid,
    _reschedule_booking_core,
    get_calendar_client,
)
from agents42.config import settings
from agents42.db import get_session, run_migrations
from agents42.integrations.google_calendar import CalendarClient, CalendarError
from agents42.models import BlockedSlot, Booking, Customer, Escalation

logger = logging.getLogger("agents42.owner_api")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
security = HTTPBasic()


def require_owner_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    """Single shared password, any username - matches the product decision
    (not per-user accounts). Fails closed if the password isn't configured,
    rather than treating an empty configured password as "no auth needed".
    """
    expected = settings.owner_dashboard_password
    if not expected or not secrets.compare_digest(credentials.password.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Incorrect credentials", headers={"WWW-Authenticate": "Basic"})


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def require_same_origin(request: Request) -> None:
    """CSRF guard for the state-changing routes. Browsers cache Basic Auth
    credentials and attach them to *any* request to this origin, including a
    form POST from a hostile page - so without this, an owner who is logged
    in and visits such a page could have bookings cancelled or slots blocked.

    Standard fetch-metadata resource isolation: trust Sec-Fetch-Site when the
    browser sends it (every current browser does), fall back to comparing
    Origin against Host when it doesn't, and allow requests with neither -
    those come from non-browser clients (curl, tests), which carry no ambient
    credentials and so can't be CSRF'd. Safe methods are exempt: following a
    link to the dashboard from elsewhere is fine.
    """
    if request.method in _SAFE_METHODS:
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        if site in ("same-origin", "none"):
            return
        raise HTTPException(status_code=403, detail="Cross-site request rejected")
    origin = request.headers.get("origin")
    if origin is None:
        return  # non-browser client
    # "null" Origin comes from sandboxed iframes and cross-site redirects,
    # never from a same-origin form, so it falls through to the mismatch.
    if urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="Cross-site request rejected")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.owner_dashboard_password:
        raise RuntimeError("OWNER_DASHBOARD_PASSWORD is not set - refusing to start unauthenticated.")
    if not settings.owner_dashboard_business_id:
        raise RuntimeError("OWNER_DASHBOARD_BUSINESS_ID is not set.")
    run_migrations()
    yield


app = FastAPI(title="agents42 owner dashboard", lifespan=lifespan)
# Everything except /health requires the owner password - unauthenticated
# liveness checks match the customer-facing app's /health, and leak nothing
# (just {"status": "ok"}).
router = APIRouter(dependencies=[Depends(require_owner_auth), Depends(require_same_origin)])


def _redirect_home(message: str, level: str = "info") -> RedirectResponse:
    return RedirectResponse(f"/?{urlencode({'message': message, 'level': level})}", status_code=303)


def _to_business_tz(value: datetime, tz: ZoneInfo) -> datetime:
    """For human display in the template, unlike _as_aware (imported from
    api.py) which only *reattaches* a timezone to an already-naive value for
    internal comparison purposes. Postgres returns TIMESTAMPTZ columns as
    UTC-aware datetimes - _as_aware alone leaves those in UTC, which would
    show e.g. "05:00" in the template for an actual 1pm appointment.
    astimezone() on an already-business-tz value (the SQLite/naive case,
    after _as_aware reattaches it) is a harmless no-op.
    """
    return _as_aware(value, tz).astimezone(tz)


def _customers_for_business(session: Session, business_id: str) -> list[Customer]:
    """Only customers with at least one booking for *this* business - a
    Customer row isn't itself business-scoped (same phone/customer_id can
    have bookings with other agents42 businesses too, see AGENTS42.md), so
    this is the boundary that keeps a different business's customers from
    ever appearing here, same reasoning as every other business_id check in
    this project.
    """
    return list(
        session.scalars(
            select(Customer).join(Booking, Booking.customer_id == Customer.id).where(Booking.business_id == business_id).distinct().order_by(Customer.name)
        ).all()
    )


def _customer_summary(customer: Customer, business_id: str, tz: ZoneInfo) -> dict:
    business_bookings = [b for b in customer.bookings if b.business_id == business_id]
    business_bookings.sort(key=lambda b: _as_aware(b.start_time, tz), reverse=True)
    last = business_bookings[0] if business_bookings else None
    return {
        "customer": customer,
        "booking_count": len(business_bookings),
        "last_booking_start": _to_business_tz(last.start_time, tz) if last else None,
    }


def _booking_history(customer: Customer, business_id: str, tz: ZoneInfo, now: datetime) -> list[dict]:
    bookings = [b for b in customer.bookings if b.business_id == business_id]
    bookings.sort(key=lambda b: _as_aware(b.start_time, tz), reverse=True)
    history = []
    for b in bookings:
        start = _to_business_tz(b.start_time, tz)
        if b.status == "cancelled":
            display_status = "cancelled"
        elif start < now:
            # There's no separate "completed" status in the schema (only
            # confirmed/cancelled) - derived here for display only, same
            # documented-heuristic approach as "recent activity" above.
            display_status = "completed"
        else:
            display_status = "upcoming"
        history.append({"booking": b, "start": start, "end": _to_business_tz(b.end_time, tz), "display_status": display_status})
    return history


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/")
def dashboard(
    request: Request,
    message: str | None = None,
    level: str = "info",
    avail_date: date_type | None = None,
    avail_service: str | None = None,
    customer_q: str | None = None,
    session: Session = Depends(get_session),
    calendar_client: CalendarClient = Depends(get_calendar_client),
):
    business_id = settings.owner_dashboard_business_id
    profile = _load_profile(business_id)
    tz = ZoneInfo(profile.timezone)
    now = datetime.now(tz)
    today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=tz)
    today_end = today_start + timedelta(days=1)

    confirmed = session.scalars(
        select(Booking)
        .where(Booking.business_id == business_id, Booking.status == "confirmed")
        .order_by(Booking.start_time)
    ).all()
    today_bookings = [b for b in confirmed if today_start <= _as_aware(b.start_time, tz) < today_end]
    upcoming_bookings = [b for b in confirmed if _as_aware(b.start_time, tz) >= today_end]

    recent = session.scalars(
        select(Booking).where(Booking.business_id == business_id).order_by(Booking.updated_at.desc()).limit(20)
    ).all()
    # "Recently changed" heuristic: there's no dedicated reschedule-history
    # table (out of scope here), so this is the only signal available -
    # cancelled, or updated after it was created. Documented limitation, not
    # a silent assumption; see DEVELOPMENT.md "Owner dashboard".
    recent_activity = [b for b in recent if b.status == "cancelled" or b.updated_at != b.created_at]

    open_escalations = session.scalars(
        select(Escalation)
        .where(Escalation.business_id == business_id, Escalation.status == "open")
        .order_by(Escalation.created_at.desc())
    ).all()
    resolved_escalations = session.scalars(
        select(Escalation)
        .where(Escalation.business_id == business_id, Escalation.status == "resolved")
        .order_by(Escalation.created_at.desc())
        .limit(20)
    ).all()
    blocked_slots = session.scalars(
        select(BlockedSlot)
        .where(BlockedSlot.business_id == business_id, BlockedSlot.end_time >= now)
        .order_by(BlockedSlot.start_time)
    ).all()

    all_customers = _customers_for_business(session, business_id)
    if customer_q:
        needle = customer_q.strip().lower()
        all_customers = [c for c in all_customers if needle in c.name.lower() or needle in c.phone.lower()]
    customer_rows = [_customer_summary(c, business_id, tz) for c in all_customers]

    available_slots: list = []
    avail_error: str | None = None
    if avail_date and avail_service:
        service = profile.services.get(avail_service)
        if service is None:
            avail_error = f"Unknown service {avail_service!r}"
        else:
            try:
                available_slots = _compute_available_slots(profile, service, avail_date, None, calendar_client)
            except CalendarError as exc:
                avail_error = str(exc)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "profile": profile,
            "today_bookings": today_bookings,
            "upcoming_bookings": upcoming_bookings,
            "recent_activity": recent_activity,
            "open_escalations": open_escalations,
            "resolved_escalations": resolved_escalations,
            "blocked_slots": blocked_slots,
            "services": profile.services,
            "avail_date": avail_date,
            "avail_service": avail_service,
            "available_slots": available_slots,
            "avail_error": avail_error,
            "customer_q": customer_q,
            "customer_rows": customer_rows,
            "today_count": len(today_bookings),
            "upcoming_count": len(upcoming_bookings),
            "attention_count": len(open_escalations),
            "blocked_count": len(blocked_slots),
            "message": message,
            "level": level,
            "to_local": lambda d: _to_business_tz(d, tz),
        },
    )


@router.get("/customers/{customer_id}")
def customer_detail(request: Request, customer_id: str, session: Session = Depends(get_session)):
    business_id = settings.owner_dashboard_business_id
    profile = _load_profile(business_id)
    tz = ZoneInfo(profile.timezone)

    customer = session.get(Customer, _parse_uuid(customer_id, field="customer_id"))
    history = _booking_history(customer, business_id, tz, datetime.now(tz)) if customer else []
    if customer is None or not history:
        # No bookings with this business at all - either a bad id, or a
        # customer who only has bookings with a *different* business (same
        # phone/customer_id, different business_id - see
        # _customers_for_business) - same 404 either way, don't distinguish.
        raise HTTPException(status_code=404, detail="Customer not found")

    return templates.TemplateResponse(
        request,
        "customer_detail.html",
        {"profile": profile, "customer": customer, "history": history},
    )


@router.post("/bookings/{booking_id}/reschedule")
def owner_reschedule_booking(
    booking_id: str,
    new_start: datetime = Form(...),
    session: Session = Depends(get_session),
    calendar_client: CalendarClient = Depends(get_calendar_client),
):
    booking = session.get(Booking, _parse_uuid(booking_id, field="booking_id"))
    if booking is None or booking.business_id != settings.owner_dashboard_business_id:
        raise HTTPException(status_code=404, detail="Booking not found")
    try:
        result = _reschedule_booking_core(booking, new_start, session, calendar_client)
    except HTTPException as exc:
        return _redirect_home(str(exc.detail), level="error")
    return _redirect_home(f"Rescheduled to {result.start}.")


@router.post("/bookings/{booking_id}/cancel")
def owner_cancel_booking(
    booking_id: str,
    session: Session = Depends(get_session),
    calendar_client: CalendarClient = Depends(get_calendar_client),
):
    booking = session.get(Booking, _parse_uuid(booking_id, field="booking_id"))
    if booking is None or booking.business_id != settings.owner_dashboard_business_id:
        raise HTTPException(status_code=404, detail="Booking not found")
    try:
        _cancel_booking_core(booking, session, calendar_client)
    except HTTPException as exc:
        return _redirect_home(str(exc.detail), level="error")
    return _redirect_home("Booking cancelled.")


@router.post("/block-time")
def create_blocked_slot(
    start: datetime = Form(...),
    end: datetime = Form(...),
    reason: str | None = Form(None),
    session: Session = Depends(get_session),
    calendar_client: CalendarClient = Depends(get_calendar_client),
):
    business_id = settings.owner_dashboard_business_id
    profile = _load_profile(business_id)
    tz = ZoneInfo(profile.timezone)
    start = start.replace(tzinfo=tz) if start.tzinfo is None else start.astimezone(tz)
    end = end.replace(tzinfo=tz) if end.tzinfo is None else end.astimezone(tz)
    if end <= start:
        return _redirect_home("End time must be after start time.", level="error")

    try:
        event_id = calendar_client.create_event(
            profile.calendar_id,
            summary=f"Blocked: {reason or 'Unavailable'}",
            start=start,
            end=end,
            description="Blocked via agents42 owner dashboard",
        )
    except CalendarError as exc:
        return _redirect_home(f"Calendar unavailable, time not blocked: {exc}", level="error")

    blocked = BlockedSlot(business_id=business_id, start_time=start, end_time=end, reason=reason, google_event_id=event_id)
    session.add(blocked)
    try:
        session.commit()
    except Exception:
        session.rollback()
        logger.error("Blocked-slot DB write failed after Calendar event %s was created - deleting it.", event_id)
        try:
            calendar_client.delete_event(profile.calendar_id, event_id)
        except CalendarError:
            logger.critical(
                "Failed to delete orphaned Calendar block event %s after a DB failure - requires manual cleanup.",
                event_id,
            )
        return _redirect_home("Could not save the block. Please try again.", level="error")
    return _redirect_home(f"Blocked {start.isoformat()} - {end.isoformat()}.")


@router.post("/blocked-slots/{blocked_id}/unblock")
def unblock(
    blocked_id: str,
    session: Session = Depends(get_session),
    calendar_client: CalendarClient = Depends(get_calendar_client),
):
    blocked = session.get(BlockedSlot, _parse_uuid(blocked_id, field="blocked_id"))
    if blocked is None or blocked.business_id != settings.owner_dashboard_business_id:
        raise HTTPException(status_code=404, detail="Blocked slot not found")
    profile = _load_profile(blocked.business_id)

    # Fail closed, same ordering as cancel_booking: delete the Calendar hold
    # before removing the DB row, since Calendar free/busy is what
    # availability search actually checks.
    if blocked.google_event_id:
        try:
            calendar_client.delete_event(profile.calendar_id, blocked.google_event_id)
        except CalendarError as exc:
            return _redirect_home(f"Calendar unavailable, block not removed: {exc}", level="error")

    session.delete(blocked)
    try:
        session.commit()
    except Exception:
        session.rollback()
        # Lower stakes than a booking cancellation gone wrong: the Calendar
        # hold is already gone, so worst case this time now looks free again
        # until someone notices - not a customer-facing inconsistency.
        # Accepted simpler gap: log critical rather than replicate
        # cancel_booking's full recreate-on-DB-failure compensation.
        logger.critical(
            "Deleted Calendar block event %s but failed to remove blocked_slot %s - requires manual cleanup.",
            blocked.google_event_id,
            blocked.id,
        )
        return _redirect_home("Calendar block removed, but the record could not be updated - contact a developer.", level="error")
    return _redirect_home("Block removed.")


@router.post("/escalations/{escalation_id}/resolve")
def resolve_escalation(escalation_id: str, session: Session = Depends(get_session)):
    escalation = session.get(Escalation, _parse_uuid(escalation_id, field="escalation_id"))
    if escalation is None or escalation.business_id != settings.owner_dashboard_business_id:
        raise HTTPException(status_code=404, detail="Escalation not found")
    escalation.status = "resolved"
    session.commit()
    return _redirect_home("Marked resolved.")


app.include_router(router)
