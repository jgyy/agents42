"""Unit tests for GoogleCalendarClient's own exception handling - not the
CalendarClient protocol in general, which is exercised via FakeCalendarClient
everywhere else (this class is "Never used in unit tests" per its own
docstring, for anything that needs real credentials/network).

This specifically covers the boundary between the real Google API client
library and this project's CalendarError contract, reproducing a real
production incident: an ssl.SSLError from the underlying httplib2
connection (a transport-level failure, before any HTTP response is
received) propagated straight past exception handling that only caught
googleapiclient.errors.HttpError, crashing two real customer booking
requests as unhandled 500s instead of the intended 502 "Calendar
unavailable, booking not confirmed" - see git history for the incident.
"""

import ssl
from datetime import datetime, timezone

import pytest
from google.auth.exceptions import RefreshError

from agents42.integrations.google_calendar import CalendarError, GoogleCalendarClient


class _RaisingCall:
    """Stands in for the chained googleapiclient call objects
    (`service.freebusy().query(...)`, `service.events().insert(...)`, ...) -
    every attribute access or call just returns itself, so any chain shape
    works, and .execute() raises whatever error the test is simulating.
    """

    def __init__(self, error: Exception):
        self._error = error

    def __getattr__(self, name):
        return lambda *args, **kwargs: self

    def execute(self):
        raise self._error


@pytest.fixture
def client():
    return GoogleCalendarClient(credentials_path="unused", token_path="unused")


def _patch_service(client, monkeypatch, error: Exception) -> None:
    # _get_service() is where real credential/network access would happen -
    # patching it out is what makes these tests run without a real Google
    # account, consistent with this class never being used in other tests.
    monkeypatch.setattr(client, "_get_service", lambda: _RaisingCall(error))


@pytest.mark.parametrize(
    "error",
    [
        ssl.SSLError("record layer failure"),  # the exact error hit in production
        ConnectionResetError("connection reset by peer"),
        TimeoutError("timed out"),
        RefreshError("invalid_grant: Token has been expired or revoked."),  # also hit in production
    ],
)
def test_get_busy_periods_wraps_transport_and_auth_errors(client, monkeypatch, error):
    _patch_service(client, monkeypatch, error)
    with pytest.raises(CalendarError):
        client.get_busy_periods("primary", datetime.now(timezone.utc), datetime.now(timezone.utc))


def test_create_event_wraps_ssl_error(client, monkeypatch):
    _patch_service(client, monkeypatch, ssl.SSLError("record layer failure"))
    with pytest.raises(CalendarError):
        client.create_event("primary", "test", datetime.now(timezone.utc), datetime.now(timezone.utc))


def test_delete_event_wraps_ssl_error(client, monkeypatch):
    _patch_service(client, monkeypatch, ssl.SSLError("record layer failure"))
    with pytest.raises(CalendarError):
        client.delete_event("primary", "evt-1")
