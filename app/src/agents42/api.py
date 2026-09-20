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
from agents42.integrations.google_calendar import CalendarClient, CalendarError, GoogleCalendarClient
from agents42.models import Booking, Customer
from agents42.profiles.loader import InvalidBusinessProfileError, UnknownBusinessError, load_business_profile
from agents42.scheduling.service import filter_by_period, find_available_slots

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
    """Normalize a DB-loaded datetime before comparing it against a
    tz-aware one computed in Python. Postgres (production) returns aware
    datetimes for TIMESTAMPTZ columns, but SQLite (used in tests) silently
    returns *naive* ones for the same column type - and critically, SQLite
    preserves the original wall-clock digits exactly as written, without
    converting to UTC first (verified: writing 13:00+08:00 reads back as
    naive 13:00, not 05:00). So the timezone to reattach is whichever one
    was used to *write* the value - always the owning business's own
    profile timezone in this codebase, never UTC. Comparing naive-vs-aware
    directly would either raise or, for `==`, silently return False, which
    would make e.g. the reschedule self-exclusion check below fail
    verbatim on SQLite while working "by accident" on Postgres.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=tz)


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


# --- Business info -----------------------------------------------------------


class ServiceInfoResponse(BaseModel):
    display_name: str
    duration_minutes: int


class OpeningHoursResponse(BaseModel):
    open: str
    close: str


class BusinessInfoResponse(BaseModel):
    id: str
    name: str
    address: str | None
    timezone: str
    services: dict[str, ServiceInfoResponse]
    opening_hours: dict[str, OpeningHoursResponse]


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
            )
            for key, svc in profile.services.items()
        },
        opening_hours={
            day: OpeningHoursResponse(open=hours.open.strftime("%H:%M"), close=hours.close.strftime("%H:%M"))
            for day, hours in profile.opening_hours.items()
        },
    )


@app.post("/availability/search", response_model=AvailabilitySearchResponse)
def search_availability(
    body: AvailabilitySearchRequest, calendar_client: CalendarClient = Depends(get_calendar_client)
) -> AvailabilitySearchResponse:
    profile, service = _load_profile_and_service(body.business_id, body.service)

    weekday = body.date.strftime("%A").lower()
    hours = profile.opening_hours.get(weekday)
    if hours is None:
        return AvailabilitySearchResponse(slots=[])  # closed that day

    tz = ZoneInfo(profile.timezone)
    query_start, query_end = _busy_query_window(body.date, hours, service.turnaround_minutes, tz)

    try:
        busy = calendar_client.get_busy_periods(profile.calendar_id, query_start, query_end)
    except CalendarError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar unavailable: {exc}") from exc

    slots = find_available_slots(
        body.date,
        hours.open,
        hours.close,
        busy,
        service.duration_minutes,
        service.turnaround_minutes,
        profile.slot_interval_minutes,
        tz,
    )
    slots = filter_by_period(slots, body.period)
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
    booking = session.get(Booking, _parse_uuid(booking_id, field="booking_id"))
    customer_uuid = _parse_uuid(body.customer_id, field="customer_id")
    if (
        booking is None
        or booking.customer_id != customer_uuid
        or booking.business_id != body.business_id
    ):
        # Don't distinguish "no such booking", "exists but isn't yours", or
        # "exists but belongs to a different business" - same 404 either
        # way. A WhatsApp session is fixed to one business (see SKILL.md);
        # without this check that business's agent could reschedule a
        # booking the same customer made with a different business.
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != "confirmed":
        raise HTTPException(status_code=422, detail=f"Booking is {booking.status!r}, not reschedulable")

    profile, service = _load_profile_and_service(booking.business_id, booking.service)
    customer = booking.customer

    tz = ZoneInfo(profile.timezone)
    new_start = body.new_start.replace(tzinfo=tz) if body.new_start.tzinfo is None else body.new_start.astimezone(tz)
    own_start, own_end = _as_aware(booking.start_time, tz), _as_aware(booking.end_time, tz)

    # Moving to the exact time the booking is already at is a legitimate
    # no-op (e.g. the customer re-confirming after list_bookings.py showed
    # them their current slot) - short-circuit before touching Calendar at
    # all, rather than trying to guess which busy period is this booking's
    # own event. Google's freebusy API only returns time ranges, not event
    # identity, so filtering busy periods by matching start/end was a real
    # risk: a different event that happens to share this exact start/end
    # (another booking, or an unrelated calendar entry) would be
    # indistinguishable from this one and could get silently hidden too.
    # For a genuinely different time, the booking's own current slot is
    # left in free/busy as-is - an overlapping target time will likely be
    # rejected as self-conflicting, an accepted limitation for now (revisit
    # with a per-event lookup via google_event_id if adjacent-time moves
    # need to be supported later).
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

    weekday = new_start.strftime("%A").lower()
    hours = profile.opening_hours.get(weekday)
    if hours is None:
        raise HTTPException(status_code=422, detail="Requested date is outside opening hours")

    query_start, query_end = _busy_query_window(new_start.date(), hours, service.turnaround_minutes, tz)
    try:
        busy = calendar_client.get_busy_periods(profile.calendar_id, query_start, query_end)
    except CalendarError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar unavailable, booking not rescheduled: {exc}") from exc

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
    booking = session.get(Booking, _parse_uuid(booking_id, field="booking_id"))
    customer_uuid = _parse_uuid(body.customer_id, field="customer_id")
    if (
        booking is None
        or booking.customer_id != customer_uuid
        or booking.business_id != body.business_id
    ):
        # Don't distinguish "no such booking", "exists but isn't yours", or
        # "exists but belongs to a different business" - same 404 either
        # way, same reasoning as reschedule_booking above.
        raise HTTPException(status_code=404, detail="Booking not found")
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
