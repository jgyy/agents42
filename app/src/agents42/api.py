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
    if booking is None or booking.customer_id != customer_uuid:
        # Don't distinguish "no such booking" from "exists but isn't yours" -
        # same 404 either way.
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != "confirmed":
        raise HTTPException(status_code=422, detail=f"Booking is {booking.status!r}, not reschedulable")

    profile, service = _load_profile_and_service(booking.business_id, booking.service)
    customer = booking.customer

    tz = ZoneInfo(profile.timezone)
    new_start = body.new_start.replace(tzinfo=tz) if body.new_start.tzinfo is None else body.new_start.astimezone(tz)

    weekday = new_start.strftime("%A").lower()
    hours = profile.opening_hours.get(weekday)
    if hours is None:
        raise HTTPException(status_code=422, detail="Requested date is outside opening hours")

    query_start, query_end = _busy_query_window(new_start.date(), hours, service.turnaround_minutes, tz)
    try:
        busy = calendar_client.get_busy_periods(profile.calendar_id, query_start, query_end)
    except CalendarError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar unavailable, booking not rescheduled: {exc}") from exc

    # This booking's own current slot must not count against itself when
    # checking the new time - it's the exact thing being moved, not a
    # conflicting third-party event. Google's freebusy API doesn't return
    # event IDs to filter by, so exclude by matching the booking's own
    # recorded start/end instead (set when it was created/last rescheduled).
    own_start, own_end = _as_aware(booking.start_time, tz), _as_aware(booking.end_time, tz)
    busy = [b for b in busy if not (b.start == own_start and b.end == own_end)]

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


@app.post("/bookings/{booking_id}/cancel", response_model=BookingResponse)
def cancel_booking(
    booking_id: str,
    body: CancelBookingRequest,
    session: Session = Depends(get_session),
    calendar_client: CalendarClient = Depends(get_calendar_client),
) -> BookingResponse:
    booking = session.get(Booking, _parse_uuid(booking_id, field="booking_id"))
    customer_uuid = _parse_uuid(body.customer_id, field="customer_id")
    if booking is None or booking.customer_id != customer_uuid:
        # Don't distinguish "no such booking" from "exists but isn't yours" -
        # same 404 either way.
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != "confirmed":
        raise HTTPException(status_code=422, detail=f"Booking is {booking.status!r}, not cancellable")

    profile = _load_profile(booking.business_id)
    event_id = booking.google_event_id

    booking.status = "cancelled"
    try:
        session.commit()
    except Exception:
        session.rollback()
        logger.error(
            "Cancellation DB write failed for booking %s - booking remains confirmed, Calendar event untouched.",
            booking.id,
        )
        raise HTTPException(status_code=500, detail="Cancellation could not be saved. Please try again.") from None

    # DB is now the source of truth - deleting the Calendar event is
    # best-effort, same reasoning as reschedule's old-event cleanup: if this
    # fails, the booking is still correctly cancelled, just a stale event
    # lingers on the calendar until someone notices and removes it manually.
    try:
        calendar_client.delete_event(profile.calendar_id, event_id)
    except CalendarError:
        logger.critical(
            "Cancelled booking %s (DB updated) but failed to delete Calendar event %s - requires manual cleanup.",
            booking.id,
            event_id,
        )

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


class CustomerBookingsResponse(BaseModel):
    bookings: list[BookingResponse]


@app.get("/customers/{customer_id}/bookings", response_model=CustomerBookingsResponse)
def list_customer_bookings(customer_id: str, session: Session = Depends(get_session)) -> CustomerBookingsResponse:
    """Upcoming, confirmed bookings only - what a reschedule flow needs to
    show ("which of your bookings?"), not a full history. A past or
    already-cancelled booking isn't actionable here.
    """
    customer = session.get(Customer, _parse_uuid(customer_id, field="customer_id"))
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    now = datetime.now(tz=ZoneInfo("UTC"))
    profiles = {}

    def profile_for(business_id: str):
        if business_id not in profiles:
            profiles[business_id] = _load_profile(business_id)
        return profiles[business_id]

    # Each booking's own timezone (needed to correctly normalize a
    # possibly-naive start_time, see _as_aware) is its business's, which can
    # differ per booking - load profiles before filtering, not after.
    upcoming = [
        b
        for b in customer.bookings
        if b.status == "confirmed" and _as_aware(b.start_time, ZoneInfo(profile_for(b.business_id).timezone)) >= now
    ]
    upcoming.sort(key=lambda b: _as_aware(b.start_time, ZoneInfo(profile_for(b.business_id).timezone)))

    results = []
    for booking in upcoming:
        profile = profiles[booking.business_id]
        tz = ZoneInfo(profile.timezone)
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
