"""Google Calendar is the source of truth for schedule availability (see
DEVELOPMENT.md). This module is the only place that talks to the Calendar
API; everything else works with the CalendarClient protocol so it can be
faked in tests without touching a real account.
"""

from datetime import datetime
from pathlib import Path
from typing import Protocol

from agents42.scheduling.service import BusyPeriod

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarError(RuntimeError):
    """Raised for any Calendar API failure. Callers must treat this as
    "availability/booking is unknown", never as "the slot is free" or
    "the booking succeeded".
    """


class CalendarClient(Protocol):
    def get_busy_periods(self, calendar_id: str, start: datetime, end: datetime) -> list[BusyPeriod]: ...

    def create_event(self, calendar_id: str, summary: str, start: datetime, end: datetime, description: str = "") -> str:
        """Returns the created event's id."""
        ...

    def delete_event(self, calendar_id: str, event_id: str) -> None: ...


class GoogleCalendarClient:
    """Real Google Calendar client. Requires a one-time local OAuth flow to
    produce `google_calendar_token_path` - see DEVELOPMENT.md ("Google
    Calendar setup"). Never used in unit tests.
    """

    def __init__(self, credentials_path: Path, token_path: Path):
        self._credentials_path = Path(credentials_path)
        self._token_path = Path(token_path)
        self._service = None

    def _get_service(self):
        if self._service is not None:
            return self._service

        # Imported lazily so the google-api-python-client dependency is only
        # required when this class is actually instantiated.
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        if not self._token_path.exists():
            raise CalendarError(
                f"No Google Calendar token at {self._token_path}. Run "
                "`python -m agents42.integrations.google_calendar_auth` locally first "
                "(see DEVELOPMENT.md)."
            )

        creds = Credentials.from_authorized_user_file(str(self._token_path), SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._token_path.write_text(creds.to_json())
            else:
                raise CalendarError(f"Google Calendar token at {self._token_path} is invalid and cannot be refreshed.")

        self._service = build("calendar", "v3", credentials=creds)
        return self._service

    def get_busy_periods(self, calendar_id: str, start: datetime, end: datetime) -> list[BusyPeriod]:
        from google.auth.exceptions import GoogleAuthError
        from googleapiclient.errors import HttpError

        try:
            service = self._get_service()
            response = (
                service.freebusy()
                .query(
                    body={
                        "timeMin": start.isoformat(),
                        "timeMax": end.isoformat(),
                        "items": [{"id": calendar_id}],
                    }
                )
                .execute()
            )
        except (HttpError, OSError, GoogleAuthError) as exc:
            # HttpError covers a proper (non-2xx) response from Google.
            # OSError (ssl.SSLError, ConnectionResetError, TimeoutError, ...
            # are all OSError subclasses) covers everything that fails
            # *before* a response is even received - a real production
            # incident hit exactly this: an ssl.SSLError from the
            # underlying httplib2 connection propagated straight past a
            # handler that only caught HttpError, crashing the request as
            # an unhandled 500 instead of the intended 502 "Calendar
            # unavailable, booking not confirmed". GoogleAuthError (its
            # subclass RefreshError specifically) covers the token itself
            # being invalid - e.g. its refresh_token was revoked, which a
            # real incident also hit: enabling 2-Step Verification on the
            # same Google account used for both Calendar and the owner
            # notification email silently invalidated the existing
            # Calendar OAuth grant.
            raise CalendarError(f"Google Calendar freebusy query failed: {exc}") from exc

        busy = response["calendars"][calendar_id].get("busy", [])
        return [
            BusyPeriod(start=datetime.fromisoformat(b["start"]), end=datetime.fromisoformat(b["end"]))
            for b in busy
        ]

    def create_event(self, calendar_id: str, summary: str, start: datetime, end: datetime, description: str = "") -> str:
        from google.auth.exceptions import GoogleAuthError
        from googleapiclient.errors import HttpError

        try:
            service = self._get_service()
            event = (
                service.events()
                .insert(
                    calendarId=calendar_id,
                    body={
                        "summary": summary,
                        "description": description,
                        "start": {"dateTime": start.isoformat()},
                        "end": {"dateTime": end.isoformat()},
                    },
                )
                .execute()
            )
        except (HttpError, OSError, GoogleAuthError) as exc:
            # See get_busy_periods above for why OSError/GoogleAuthError are
            # caught alongside HttpError.
            raise CalendarError(f"Google Calendar event creation failed: {exc}") from exc

        return event["id"]

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        from google.auth.exceptions import GoogleAuthError
        from googleapiclient.errors import HttpError

        try:
            self._get_service().events().delete(calendarId=calendar_id, eventId=event_id).execute()
        except (HttpError, OSError, GoogleAuthError) as exc:
            # See get_busy_periods above for why OSError/GoogleAuthError are
            # caught alongside HttpError.
            raise CalendarError(f"Google Calendar event deletion failed: {exc}") from exc
