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
    id: str
    phone: str
    name: str
    created: bool = False


@app.post("/customers/resolve", response_model=CustomerResponse)
def resolve_customer(body: ResolveCustomerRequest, session: Session = Depends(get_session)) -> CustomerResponse:
    try:
        customer, created = resolve_or_create_customer(session, body.phone, body.name)
    except InvalidPhoneNumber as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        # name required for a brand-new customer
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session.commit()
    return CustomerResponse(id=str(customer.id), phone=customer.phone, name=customer.name, created=created)


@app.get("/customers/{customer_id}", response_model=CustomerResponse)
def get_customer(customer_id: str, session: Session = Depends(get_session)) -> CustomerResponse:
    customer = session.get(Customer, _parse_uuid(customer_id, field="customer_id"))
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return CustomerResponse(id=str(customer.id), phone=customer.phone, name=customer.name)


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


def _load_profile_and_service(business_id: str, service_name: str):
    try:
        profile = load_business_profile(business_id)
    except UnknownBusinessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidBusinessProfileError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    service = profile.services.get(service_name)
    if service is None:
        raise HTTPException(status_code=422, detail=f"Unknown service {service_name!r} for business {business_id!r}")
    return profile, service


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
    day_start = datetime.combine(body.date, hours.open, tzinfo=tz)
    day_end = datetime.combine(body.date, hours.close, tzinfo=tz)

    try:
        busy = calendar_client.get_busy_periods(profile.calendar_id, day_start, day_end)
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

    day_start = datetime.combine(start.date(), hours.open, tzinfo=tz)
    day_end = datetime.combine(start.date(), hours.close, tzinfo=tz)

    try:
        busy = calendar_client.get_busy_periods(profile.calendar_id, day_start, day_end)
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
    return BookingResponse(
        id=str(booking.id),
        business_id=booking.business_id,
        customer_id=str(booking.customer_id),
        service=booking.service,
        start=booking.start_time.isoformat(),
        end=booking.end_time.isoformat(),
        status=booking.status,
        google_event_id=booking.google_event_id,
    )
