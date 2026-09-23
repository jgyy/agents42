"""HTTP boundary. Deliberately thin: every endpoint just validates input,
calls the deterministic service layer / calendar integration, and returns
plain JSON. OpenClaw never talks to the LLM about *whether* a slot is free -
it calls one of these endpoints (via the CLI scripts in
openclaw/workspace/skills/front-desk/scripts/) and relays the result.
"""

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import date as date_type
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents42.config import settings
from agents42.customers.service import InvalidPhoneNumber, resolve_or_create_customer
from agents42.db import get_session, run_migrations
from agents42.integrations.email_notifier import EmailError, EmailNotifier, SmtpEmailNotifier
from agents42.integrations.google_calendar import CalendarClient, CalendarError, GoogleCalendarClient
from agents42.models import Booking, Customer, Escalation
from agents42.profiles.loader import InvalidBusinessProfileError, UnknownBusinessError, load_business_profile
from agents42.scheduling.service import exclude_period, filter_by_period, find_available_slots

logger = logging.getLogger("agents42.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    yield


app = FastAPI(title="agents42 front-desk API", lifespan=lifespan)


@lru_cache
def _default_calendar_client() -> GoogleCalendarClient:
    return GoogleCalendarClient(settings.google_calendar_credentials_path, settings.google_calendar_token_path)


def get_calendar_client() -> CalendarClient:
    return _default_calendar_client()


@lru_cache
def _default_email_notifier() -> SmtpEmailNotifier:
    return SmtpEmailNotifier(
        settings.smtp_host, settings.smtp_port, settings.smtp_username, settings.smtp_password, settings.smtp_from
    )


def get_email_notifier() -> EmailNotifier | None:
    """None means "not configured" - owner_notification_email/smtp_host are
    both optional (see config.py), and a business that hasn't set up
    notifications yet must still be able to create escalations normally.
    """
    if not settings.smtp_host or not settings.owner_notification_email:
        return None
    return _default_email_notifier()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _parse_uuid(value: str, *, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid {field}: {value!r}") from exc


# --- Customers -------------------------------------------------------------


class ResolveCustomerRequest(BaseModel):
    phone: str
    name: str | None = None


class CustomerResponse(BaseModel):
    found: bool
    needs_name: bool = False
    id: str | None = None
    phone: str
    name: str | None = None
    created: bool = False


@app.post("/customers/resolve", response_model=CustomerResponse)
def resolve_customer(body: ResolveCustomerRequest, session: Session = Depends(get_session)) -> CustomerResponse:
    try:
        resolution = resolve_or_create_customer(session, body.phone, body.name)
    except InvalidPhoneNumber as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if resolution.needs_name:
        # A new phone number with no name yet is a normal step in the
        # conversation, not an error - the caller (SKILL.md's Main Plan) asks
        # for a name and calls this again, rather than parsing a 422.
        return CustomerResponse(found=False, needs_name=True, phone=resolution.phone)

    session.commit()
    customer = resolution.customer
    return CustomerResponse(
        found=True, id=str(customer.id), phone=customer.phone, name=customer.name, created=resolution.created
    )


@app.get("/customers/{customer_id}", response_model=CustomerResponse)
def get_customer(customer_id: str, session: Session = Depends(get_session)) -> CustomerResponse:
    customer = session.get(Customer, _parse_uuid(customer_id, field="customer_id"))
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return CustomerResponse(found=True, id=str(customer.id), phone=customer.phone, name=customer.name)


# --- Availability ------------------------------------------------------------


class AvailabilitySearchRequest(BaseModel):
    business_id: str
    service: str
    date: date_type
    period: Literal["morning", "afternoon", "evening"] | None = None
    # Set both when searching on behalf of a reschedule: the named booking's
    # own Calendar event is carved out of free/busy, exactly as
    # reschedule_booking does, so search offers the same slots reschedule
    # will accept. customer_id is required alongside it - a booking can only
    # be excluded by the customer who owns it, for this business.
    exclude_booking_id: str | None = None
    customer_id: str | None = None


class SlotResponse(BaseModel):
    start: str
    end: str


class AvailabilitySearchResponse(BaseModel):
    slots: list[SlotResponse]


def _load_profile(business_id: str):
    try:
        return load_business_profile(business_id)
    except UnknownBusinessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidBusinessProfileError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _load_profile_and_service(business_id: str, service_name: str):
    profile = _load_profile(business_id)
    service = profile.services.get(service_name)
    if service is None:
        raise HTTPException(status_code=422, detail=f"Unknown service {service_name!r} for business {business_id!r}")
    return profile, service


def _humanize(key: str) -> str:
    return key.replace("_", " ").title()


def _as_aware(value: datetime, tz: ZoneInfo) -> datetime:
    """Normalize a DB-loaded datetime to the business's own timezone before
    comparing it against a tz-aware one computed in Python, or rendering it
    into a response.

    The two backends hand back the same TIMESTAMPTZ column differently:

    - SQLite (tests) returns *naive* datetimes, preserving the wall-clock
      digits exactly as written without converting to UTC first (verified:
      writing 13:00+08:00 reads back as naive 13:00, not 05:00). The zone to
      reattach is whichever one *wrote* the value - always the owning
      business's profile timezone in this codebase, never UTC.
    - Postgres via psycopg (production) returns *aware* datetimes, but in
      the connection's session timezone - Etc/UTC in the stock postgres
      image - so the same 13:00+08:00 arrives as 05:00+00:00. That is the
      same instant, so `==` comparisons still pass, but `.isoformat()` on it
      renders "05:00:00+00:00", and the agent relaying that to a customer
      booked at 1pm would read out the wrong clock time. Convert it.

    Comparing naive-vs-aware directly would either raise or, for `==`,
    silently return False, which would make e.g. the reschedule
    self-exclusion check fail verbatim on SQLite while working "by
    accident" on Postgres.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _busy_query_window(date_: date_type, hours, buffer_minutes: int, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The window to ask Calendar about for a given business day.

    Google's freebusy only returns periods that intersect [timeMin, timeMax].
    `is_valid_slot` needs `buffer_minutes` of clear time around every event,
    so an event ending 15 minutes before opening (or starting 15 minutes
    after closing) still constrains the first/last slot - it just wouldn't be
    reported if we only asked about opening hours. Pad both ends by the buffer.
    """
    buffer = timedelta(minutes=buffer_minutes)
    day_start = datetime.combine(date_, hours.open, tzinfo=tz)
    day_end = datetime.combine(date_, hours.close, tzinfo=tz)
    return day_start - buffer, day_end + buffer


def _exceeds_advance_window(date_: date_type, profile) -> bool:
    """True if `date_` is further ahead than this business takes bookings
    for (BusinessProfile.max_advance_days) - e.g. "up to 3 months ahead
    only". None means no limit. Shared by availability search, booking, and
    reschedule so the window is enforced identically everywhere a date is
    accepted, not just at search time.
    """
    if profile.max_advance_days is None:
        return False
    today = datetime.now(ZoneInfo(profile.timezone)).date()
    return date_ > today + timedelta(days=profile.max_advance_days)


# --- Business info -----------------------------------------------------------


class ServiceInfoResponse(BaseModel):
    display_name: str
    duration_minutes: int
    price_from: str | None = None


class AddOnInfoResponse(BaseModel):
    display_name: str
    price_from: str | None = None


class OpeningHoursResponse(BaseModel):
    open: str
    close: str


class BusinessInfoResponse(BaseModel):
    id: str
    name: str
    address: str | None
    timezone: str
    services: dict[str, ServiceInfoResponse]
    add_ons: dict[str, AddOnInfoResponse]
    opening_hours: dict[str, OpeningHoursResponse]
    about: str | None
    pricing_note: str | None
    max_advance_days: int | None


@app.get("/businesses/{business_id}", response_model=BusinessInfoResponse)
def get_business_info(business_id: str) -> BusinessInfoResponse:
    profile = _load_profile(business_id)
    return BusinessInfoResponse(
        id=profile.id,
        name=profile.name,
        address=profile.address,
        timezone=profile.timezone,
        services={
            key: ServiceInfoResponse(
                display_name=svc.display_name or _humanize(key),
                duration_minutes=svc.duration_minutes,
                price_from=svc.price_from,
            )
            for key, svc in profile.services.items()
        },
        add_ons={
            key: AddOnInfoResponse(
                display_name=addon.display_name or _humanize(key),
                price_from=addon.price_from,
            )
            for key, addon in profile.add_ons.items()
        },
        opening_hours={
            day: OpeningHoursResponse(open=hours.open.strftime("%H:%M"), close=hours.close.strftime("%H:%M"))
            for day, hours in profile.opening_hours.items()
        },
        about=profile.about,
        pricing_note=profile.pricing_note,
        max_advance_days=profile.max_advance_days,
    )


def _load_own_booking(session: Session, booking_id: str, customer_id: str, business_id: str) -> Booking:
    """The booking a customer is acting on, or 404. Deliberately does not
    distinguish "no such booking", "exists but isn't yours", or "exists but
    belongs to a different business": a WhatsApp session is fixed to one
    business (see SKILL.md), and without the business check that business's
    agent could act on a booking the same customer made with a different
    business.
    """
    booking = session.get(Booking, _parse_uuid(booking_id, field="booking_id"))
    customer_uuid = _parse_uuid(customer_id, field="customer_id")
    if booking is None or booking.customer_id != customer_uuid or booking.business_id != business_id:
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking


def _compute_available_slots(
    profile,
    service,
    date_: date_type,
    period: str | None,
    calendar_client: CalendarClient,
    exclude: tuple[datetime, datetime] | None = None,
):
    """Shared by search_availability (customer-facing, JSON) and the owner
    dashboard's availability panel (server-rendered). Returns raw Slot
    objects, not a response model - callers decide how to present them.
    Raises CalendarError on failure rather than an HTTPException, so each
    caller can translate it into whatever response shape it needs.

    `exclude` is an aware [start, end) to subtract from Calendar free/busy:
    the booking being rescheduled still holds its own event, and free/busy
    has no event identity, so without this search would never offer "push
    it back an hour" even though reschedule_booking accepts it.
    """
    if _exceeds_advance_window(date_, profile):
        return []  # further ahead than this business takes bookings for

    weekday = date_.strftime("%A").lower()
    hours = profile.opening_hours.get(weekday)
    if hours is None:
        return []  # closed that day

    tz = ZoneInfo(profile.timezone)
    query_start, query_end = _busy_query_window(date_, hours, service.turnaround_minutes, tz)
    busy = calendar_client.get_busy_periods(profile.calendar_id, query_start, query_end)

    if exclude is not None:
        busy = exclude_period(busy, *exclude)

    slots = find_available_slots(
        date_,
        hours.open,
        hours.close,
        busy,
        service.duration_minutes,
        service.turnaround_minutes,
        profile.slot_interval_minutes,
        tz,
    )
    return filter_by_period(slots, period)


@app.post("/availability/search", response_model=AvailabilitySearchResponse)
def search_availability(
    body: AvailabilitySearchRequest,
    session: Session = Depends(get_session),
    calendar_client: CalendarClient = Depends(get_calendar_client),
) -> AvailabilitySearchResponse:
    profile, service = _load_profile_and_service(body.business_id, body.service)
    tz = ZoneInfo(profile.timezone)

    # Resolve the booking being moved *before* computing slots (which has
    # its own closed-day / advance-window early returns) so a bad id is a
    # 404/422 regardless of which date was asked about.
    own_period: tuple[datetime, datetime] | None = None
    if body.exclude_booking_id is not None:
        if body.customer_id is None:
            raise HTTPException(status_code=422, detail="customer_id is required with exclude_booking_id")
        own = _load_own_booking(session, body.exclude_booking_id, body.customer_id, body.business_id)
        if own.status != "confirmed":
            # Same guard as reschedule_booking. A cancelled booking has no
            # Calendar event any more, so its old [start, end) is nobody's
            # hold to carve out - whoever booked that time since would be
            # erased from free/busy and offered a slot that isn't free.
            raise HTTPException(status_code=422, detail=f"Booking is {own.status!r}, not reschedulable")
        own_period = (_as_aware(own.start_time, tz), _as_aware(own.end_time, tz))

    try:
        slots = _compute_available_slots(profile, service, body.date, body.period, calendar_client, exclude=own_period)
    except CalendarError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar unavailable: {exc}") from exc
    return AvailabilitySearchResponse(slots=[SlotResponse(start=s.start.isoformat(), end=s.end.isoformat()) for s in slots])

# --- Bookings ----------------------------------------------------------------


class CreateBookingRequest(BaseModel):
    business_id: str
    customer_id: str
    service: str
    start: datetime


class BookingResponse(BaseModel):
    id: str
    business_id: str
    business_name: str
    customer_id: str
    service: str
    start: str
    end: str
    status: str
    google_event_id: str | None


@app.post("/bookings", response_model=BookingResponse, status_code=201)
def create_booking(
    body: CreateBookingRequest,
    session: Session = Depends(get_session),
    calendar_client: CalendarClient = Depends(get_calendar_client),
) -> BookingResponse:
    profile, service = _load_profile_and_service(body.business_id, body.service)

    customer = session.get(Customer, _parse_uuid(body.customer_id, field="customer_id"))
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    tz = ZoneInfo(profile.timezone)
    start = body.start.replace(tzinfo=tz) if body.start.tzinfo is None else body.start.astimezone(tz)

    if _exceeds_advance_window(start.date(), profile):
        raise HTTPException(
            status_code=422,
            detail=f"Requested date is more than {profile.max_advance_days} days in advance",
        )

    weekday = start.strftime("%A").lower()
    hours = profile.opening_hours.get(weekday)
    if hours is None:
        raise HTTPException(status_code=422, detail="Requested date is outside opening hours")

    query_start, query_end = _busy_query_window(start.date(), hours, service.turnaround_minutes, tz)

    try:
        busy = calendar_client.get_busy_periods(profile.calendar_id, query_start, query_end)
    except CalendarError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar unavailable, booking not confirmed: {exc}") from exc

    # Recheck immediately before booking, and validate the requested start the
    # same way availability search does - reusing find_available_slots (rather
    # than re-deriving opening-hours/buffer/interval rules here a second time)
    # is what rejects a past start time and a start that doesn't land on
    # slot_interval_minutes, not just a Calendar conflict. Never trust
    # availability computed for an earlier reply in the conversation.
    valid_slots = find_available_slots(
        start.date(),
        hours.open,
        hours.close,
        busy,
        service.duration_minutes,
        service.turnaround_minutes,
        profile.slot_interval_minutes,
        tz,
    )
    matching_slot = next((slot for slot in valid_slots if slot.start == start), None)
    if matching_slot is None:
        raise HTTPException(status_code=409, detail="Requested slot is no longer available")
    end = matching_slot.end

    try:
        event_id = calendar_client.create_event(
            profile.calendar_id,
            summary=f"{body.service} - {customer.name}",
            start=start,
            end=end,
            description=f"Booked via agents42 for {customer.name} ({customer.phone})",
        )
    except CalendarError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar unavailable, booking not confirmed: {exc}") from exc

    booking = Booking(
        customer_id=customer.id,
        business_id=profile.id,
        service=body.service,
        start_time=start,
        end_time=end,
        status="confirmed",
        google_event_id=event_id,
    )
    session.add(booking)
    try:
        session.commit()
    except Exception:
        session.rollback()
        logger.error(
            "Booking DB write failed after Calendar event %s was created for customer %s - "
            "deleting the orphaned Calendar event so it doesn't advertise a phantom booking.",
            event_id,
            customer.id,
        )
        try:
            calendar_client.delete_event(profile.calendar_id, event_id)
        except CalendarError:
            logger.critical(
                "Failed to delete orphaned Calendar event %s after a DB failure - "
                "requires manual reconciliation.",
                event_id,
            )
        raise HTTPException(status_code=500, detail="Booking could not be saved. Please try again.") from None

    return BookingResponse(
        id=str(booking.id),
        business_id=booking.business_id,
        business_name=profile.name,
        customer_id=str(booking.customer_id),
        service=booking.service,
        start=booking.start_time.isoformat(),
        end=booking.end_time.isoformat(),
        status=booking.status,
        google_event_id=booking.google_event_id,
    )


@app.get("/bookings/{booking_id}", response_model=BookingResponse)
def get_booking(booking_id: str, session: Session = Depends(get_session)) -> BookingResponse:
    booking = session.get(Booking, _parse_uuid(booking_id, field="booking_id"))
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    profile = _load_profile(booking.business_id)
    tz = ZoneInfo(profile.timezone)
    return BookingResponse(
        id=str(booking.id),
        business_id=booking.business_id,
        business_name=profile.name,
        customer_id=str(booking.customer_id),
        service=booking.service,
        start=_as_aware(booking.start_time, tz).isoformat(),
        end=_as_aware(booking.end_time, tz).isoformat(),
        status=booking.status,
        google_event_id=booking.google_event_id,
    )


class RescheduleBookingRequest(BaseModel):
    customer_id: str
    business_id: str
    new_start: datetime


@app.post("/bookings/{booking_id}/reschedule", response_model=BookingResponse)
def reschedule_booking(
    booking_id: str,
    body: RescheduleBookingRequest,
    session: Session = Depends(get_session),
    calendar_client: CalendarClient = Depends(get_calendar_client),
) -> BookingResponse:
    booking = _load_own_booking(session, booking_id, body.customer_id, body.business_id)
    return _reschedule_booking_core(booking, body.new_start, session, calendar_client)


def _reschedule_booking_core(
    booking: Booking, new_start: datetime, session: Session, calendar_client: CalendarClient
) -> BookingResponse:
    """The actual reschedule logic, once the caller has already established
    it's allowed to act on this booking. Shared by the customer-facing
    endpoint above (customer_id + business_id checked) and the owner
    dashboard's reschedule action (business_id checked only) - the
    Calendar+DB compensation logic below is identical either way, so it
    must not be duplicated between the two authority levels.
    """
    if booking.status != "confirmed":
        raise HTTPException(status_code=422, detail=f"Booking is {booking.status!r}, not reschedulable")

    profile, service = _load_profile_and_service(booking.business_id, booking.service)
    customer = booking.customer

    tz = ZoneInfo(profile.timezone)
    new_start = new_start.replace(tzinfo=tz) if new_start.tzinfo is None else new_start.astimezone(tz)
    own_start, own_end = _as_aware(booking.start_time, tz), _as_aware(booking.end_time, tz)

    # Moving to the exact time the booking is already at is a legitimate
    # no-op (e.g. the customer re-confirming after list_bookings.py showed
    # them their current slot) - short-circuit before touching Calendar at
    # all.
    if new_start == own_start:
        return BookingResponse(
            id=str(booking.id),
            business_id=booking.business_id,
            business_name=profile.name,
            customer_id=str(booking.customer_id),
            service=booking.service,
            start=own_start.isoformat(),
            end=own_end.isoformat(),
            status=booking.status,
            google_event_id=booking.google_event_id,
        )

    if _exceeds_advance_window(new_start.date(), profile):
        raise HTTPException(
            status_code=422,
            detail=f"Requested date is more than {profile.max_advance_days} days in advance",
        )

    weekday = new_start.strftime("%A").lower()
    hours = profile.opening_hours.get(weekday)
    if hours is None:
        raise HTTPException(status_code=422, detail="Requested date is outside opening hours")

    query_start, query_end = _busy_query_window(new_start.date(), hours, service.turnaround_minutes, tz)
    try:
        busy = calendar_client.get_busy_periods(profile.calendar_id, query_start, query_end)
    except CalendarError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar unavailable, booking not rescheduled: {exc}") from exc

    # The booking's own Calendar event is still there and shows up in
    # free/busy, so without this a move to any time overlapping its current
    # slot (or its trailing buffer) would be refused as a conflict with
    # itself - e.g. "push my 1pm back to 2pm". Free/busy returns merged time
    # ranges with no event identity, so we can't drop "the booking's event";
    # instead subtract exactly its own [start, end) from every range. Any
    # neighbouring event coalesced into the same range survives as the
    # leftover piece and still blocks, as it should.
    busy = exclude_period(busy, own_start, own_end)

    # Recheck immediately before rescheduling, same reasoning as create_booking.
    valid_slots = find_available_slots(
        new_start.date(),
        hours.open,
        hours.close,
        busy,
        service.duration_minutes,
        service.turnaround_minutes,
        profile.slot_interval_minutes,
        tz,
    )
    matching_slot = next((slot for slot in valid_slots if slot.start == new_start), None)
    if matching_slot is None:
        raise HTTPException(status_code=409, detail="Requested slot is no longer available")
    new_end = matching_slot.end

    try:
        new_event_id = calendar_client.create_event(
            profile.calendar_id,
            summary=f"{booking.service} - {customer.name}",
            start=new_start,
            end=new_end,
            description=f"Booked via agents42 for {customer.name} ({customer.phone})",
        )
    except CalendarError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar unavailable, booking not rescheduled: {exc}") from exc

    old_event_id = booking.google_event_id
    booking.start_time = new_start
    booking.end_time = new_end
    booking.google_event_id = new_event_id
    try:
        session.commit()
    except Exception:
        session.rollback()
        logger.error(
            "Reschedule DB write failed after new Calendar event %s was created for booking %s - "
            "deleting the orphaned new event, leaving the original booking untouched.",
            new_event_id,
            booking.id,
        )
        try:
            calendar_client.delete_event(profile.calendar_id, new_event_id)
        except CalendarError:
            logger.critical(
                "Failed to delete orphaned Calendar event %s after a reschedule DB failure - "
                "requires manual reconciliation.",
                new_event_id,
            )
        raise HTTPException(status_code=500, detail="Reschedule could not be saved. Please try again.") from None

    # DB is now the source of truth for the new time - clean up the old
    # Calendar event. Best-effort: if this fails, the booking data itself is
    # already correct, just a stale duplicate event lingers on the calendar
    # until someone notices and removes it manually.
    try:
        calendar_client.delete_event(profile.calendar_id, old_event_id)
    except CalendarError:
        logger.critical(
            "Rescheduled booking %s (DB updated, new Calendar event %s created) but failed to "
            "delete the old Calendar event %s - requires manual cleanup.",
            booking.id,
            new_event_id,
            old_event_id,
        )

    return BookingResponse(
        id=str(booking.id),
        business_id=booking.business_id,
        business_name=profile.name,
        customer_id=str(booking.customer_id),
        service=booking.service,
        start=booking.start_time.isoformat(),
        end=booking.end_time.isoformat(),
        status=booking.status,
        google_event_id=booking.google_event_id,
    )


class CancelBookingRequest(BaseModel):
    customer_id: str
    business_id: str


@app.post("/bookings/{booking_id}/cancel", response_model=BookingResponse)
def cancel_booking(
    booking_id: str,
    body: CancelBookingRequest,
    session: Session = Depends(get_session),
    calendar_client: CalendarClient = Depends(get_calendar_client),
) -> BookingResponse:
    booking = _load_own_booking(session, booking_id, body.customer_id, body.business_id)
    return _cancel_booking_core(booking, session, calendar_client)


def _cancel_booking_core(booking: Booking, session: Session, calendar_client: CalendarClient) -> BookingResponse:
    """The actual cancellation logic, once the caller has already
    established it's allowed to act on this booking. Shared by the
    customer-facing endpoint above and the owner dashboard's cancel action,
    same reasoning as _reschedule_booking_core.
    """
    if booking.status != "confirmed":
        raise HTTPException(status_code=422, detail=f"Booking is {booking.status!r}, not cancellable")

    profile = _load_profile(booking.business_id)
    customer = booking.customer
    tz = ZoneInfo(profile.timezone)
    start, end = _as_aware(booking.start_time, tz), _as_aware(booking.end_time, tz)
    old_event_id = booking.google_event_id

    # Delete the Calendar event *before* marking the DB row cancelled, not
    # after. Google Calendar free/busy is what availability search actually
    # checks - a cancellation that commits the DB first but then fails to
    # delete the event would leave that slot looking permanently occupied
    # even though the booking itself shows cancelled, which is worse than
    # just failing the request outright. If deletion fails, nothing has
    # changed yet, so fail closed rather than best-effort.
    try:
        calendar_client.delete_event(profile.calendar_id, old_event_id)
    except CalendarError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar unavailable, booking not cancelled: {exc}") from exc

    booking.status = "cancelled"
    try:
        session.commit()
    except Exception:
        session.rollback()
        logger.error(
            "Cancellation DB write failed for booking %s after its Calendar event %s was already deleted - "
            "recreating an equivalent event so the booking and calendar stay consistent.",
            booking.id,
            old_event_id,
        )
        try:
            restored_event_id = calendar_client.create_event(
                profile.calendar_id,
                summary=f"{booking.service} - {customer.name}",
                start=start,
                end=end,
                description=f"Booked via agents42 for {customer.name} ({customer.phone})",
            )
        except CalendarError:
            # Nothing recovered: the original event is gone, no replacement
            # exists, and the DB still shows this booking confirmed - it has
            # no Calendar hold on its time at all. Manual fix: recreate the
            # event and point the booking at it (or cancel it properly).
            logger.critical(
                "Failed to recreate Calendar event for booking %s after a cancellation DB failure - the "
                "original event %s is gone and no replacement could be created either - the booking has no "
                "Calendar hold on its time at all and requires manual recreation.",
                booking.id,
                old_event_id,
            )
        else:
            booking.google_event_id = restored_event_id
            try:
                session.commit()
            except Exception:
                session.rollback()
                # Different situation from the branch above: the
                # replacement event genuinely exists on Calendar now, but
                # persisting its id back to the booking failed - so the DB
                # still points at the deleted original event id, and this
                # new replacement event is untracked by the DB entirely.
                # Manual fix: update the booking's google_event_id to the
                # new event, not recreate anything.
                logger.critical(
                    "Recreated Calendar event %s for booking %s after a cancellation DB failure, but failed "
                    "to persist its id - the booking's DB row still points at the deleted original event %s "
                    "while a live, untracked replacement event %s now exists on Calendar - requires manual "
                    "reconciliation to update the booking's google_event_id.",
                    restored_event_id,
                    booking.id,
                    old_event_id,
                    restored_event_id,
                )
        raise HTTPException(status_code=500, detail="Cancellation could not be saved. Please try again.") from None

    return BookingResponse(
        id=str(booking.id),
        business_id=booking.business_id,
        business_name=profile.name,
        customer_id=str(booking.customer_id),
        service=booking.service,
        start=_as_aware(booking.start_time, tz).isoformat(),
        end=_as_aware(booking.end_time, tz).isoformat(),
        status=booking.status,
        google_event_id=booking.google_event_id,
    )


class CreateEscalationRequest(BaseModel):
    business_id: str
    customer_id: str | None = None
    booking_id: str | None = None
    reason: str
    detail: str | None = None


class EscalationResponse(BaseModel):
    id: str
    business_id: str
    customer_id: str | None
    booking_id: str | None
    reason: str
    detail: str | None
    status: str


def _mask_phone_for_email(phone: str) -> str:
    """Partial mask for the less-trusted email channel - the dashboard
    already holds the full number for anyone who needs it there.
    """
    if len(phone) <= 7:
        return phone
    return f"{phone[:3]}{'*' * (len(phone) - 7)}{phone[-4:]}"


def _send_escalation_email(
    email_notifier: EmailNotifier, profile, escalation: Escalation, customer: Customer | None, booking: Booking | None
) -> None:
    lines = [f"{profile.name} has a customer escalation.", ""]
    if customer is not None:
        lines.append(f"Customer: {customer.name}")
        lines.append(f"Phone: {_mask_phone_for_email(customer.phone)}")
    else:
        lines.append("Customer: (not yet identified)")
    lines.append(f"Reason: {escalation.reason}")
    if escalation.detail:
        lines.append(f"Details: {escalation.detail}")
    if booking is not None:
        tz = ZoneInfo(profile.timezone)
        start = _as_aware(booking.start_time, tz)
        lines += ["", "Booking:", start.strftime("%d %b %Y, %I:%M %p"), booking.service]
    lines += ["", "Please review the Attention queue in the owner dashboard."]

    try:
        email_notifier.send(
            to_addr=settings.owner_notification_email,
            subject=f"[Agents42] Customer needs attention - {profile.name}",
            body="\n".join(lines),
        )
    except EmailError:
        # Best-effort only - the escalation is already committed and
        # visible in the dashboard's Attention queue regardless of whether
        # this email goes out. Never let a notification failure look like
        # the escalation itself failed.
        logger.warning("Failed to send owner notification email for escalation %s.", escalation.id)


@app.post("/escalations", response_model=EscalationResponse, status_code=201)
def create_escalation(
    body: CreateEscalationRequest,
    session: Session = Depends(get_session),
    email_notifier: EmailNotifier | None = Depends(get_email_notifier),
) -> EscalationResponse:
    """Called by the front-desk skill whenever it escalates to a human
    (flag_attention.py) - this is the only thing that makes an escalation
    visible anywhere beyond the WhatsApp conversation itself. Feeds the
    owner dashboard's Attention panel, and best-effort emails the owner if
    notifications are configured (see get_email_notifier) - the dashboard
    stays the source of truth regardless of whether the email succeeds.
    """
    profile = _load_profile(body.business_id)  # 404 on unknown business, same as every other endpoint
    customer_uuid = _parse_uuid(body.customer_id, field="customer_id") if body.customer_id else None
    booking_uuid = _parse_uuid(body.booking_id, field="booking_id") if body.booking_id else None
    customer = session.get(Customer, customer_uuid) if customer_uuid is not None else None
    if customer_uuid is not None and customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    booking = session.get(Booking, booking_uuid) if booking_uuid is not None else None
    if booking_uuid is not None and booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")

    escalation = Escalation(
        business_id=body.business_id,
        customer_id=customer_uuid,
        booking_id=booking_uuid,
        reason=body.reason,
        detail=body.detail,
        status="open",
    )
    session.add(escalation)
    session.commit()

    if email_notifier is not None:
        _send_escalation_email(email_notifier, profile, escalation, customer, booking)

    return EscalationResponse(
        id=str(escalation.id),
        business_id=escalation.business_id,
        customer_id=str(escalation.customer_id) if escalation.customer_id else None,
        booking_id=str(escalation.booking_id) if escalation.booking_id else None,
        reason=escalation.reason,
        detail=escalation.detail,
        status=escalation.status,
    )


class CustomerBookingsResponse(BaseModel):
    bookings: list[BookingResponse]


@app.get("/customers/{customer_id}/bookings", response_model=CustomerBookingsResponse)
def list_customer_bookings(
    customer_id: str, business_id: str, session: Session = Depends(get_session)
) -> CustomerBookingsResponse:
    """Upcoming, confirmed bookings for one business only - what a
    reschedule/cancel flow needs to show ("which of your bookings?"), not a
    full history. A past or already-cancelled booking isn't actionable here.

    `business_id` is required, not optional: a customer can have bookings
    with more than one agents42 business (same phone, same `customer_id`
    everywhere - see `resolve_customer.py`), and a WhatsApp session is
    always scoped to exactly one business. Without this filter, Business
    A's agent could see - and, via reschedule/cancel, modify - a booking
    that belongs to Business B.
    """
    customer = session.get(Customer, _parse_uuid(customer_id, field="customer_id"))
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    profile = _load_profile(business_id)
    tz = ZoneInfo(profile.timezone)
    now = datetime.now(tz=ZoneInfo("UTC"))

    upcoming = [
        b
        for b in customer.bookings
        if b.business_id == business_id and b.status == "confirmed" and _as_aware(b.start_time, tz) >= now
    ]
    upcoming.sort(key=lambda b: _as_aware(b.start_time, tz))

    results = []
    for booking in upcoming:
        start = _as_aware(booking.start_time, tz)
        end = _as_aware(booking.end_time, tz)
        results.append(
            BookingResponse(
                id=str(booking.id),
                business_id=booking.business_id,
                business_name=profile.name,
                customer_id=str(booking.customer_id),
                service=booking.service,
                start=start.isoformat(),
                end=end.isoformat(),
                status=booking.status,
                google_event_id=booking.google_event_id,
            )
        )
    return CustomerBookingsResponse(bookings=results)
