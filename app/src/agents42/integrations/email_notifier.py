"""Best-effort owner notification when the front-desk skill escalates to a
human (see api.py:create_escalation). Deterministic side effect, never an
LLM in this path - the escalation itself is already durably committed to
the DB before this ever runs, so a notification failure can only mean a
missed email, never a lost escalation. Everything else works with the
EmailNotifier protocol so it can be faked in tests without touching a real
SMTP account.
"""

import smtplib
from email.message import EmailMessage
from typing import Protocol


class EmailError(RuntimeError):
    """Raised for any notification-send failure. Callers must treat this as
    "the owner wasn't emailed", never as a reason to undo or hide the
    escalation itself - the dashboard's Attention queue is the source of
    truth regardless of whether this succeeds.
    """


class EmailNotifier(Protocol):
    def send(self, to_addr: str, subject: str, body: str) -> None: ...


class SmtpEmailNotifier:
    """Plain SMTP+STARTTLS, deliberately not a provider-specific API client
    (Gmail API, SES SDK, ...) - a standalone app password on whatever
    mailbox the business already checks is enough for this scale, and
    keeps the credential narrowly scoped to "can send mail" rather than
    reusing/widening a different integration's credentials (e.g. the
    Calendar OAuth token) for an unrelated purpose.
    """

    def __init__(self, host: str, port: int, username: str, password: str, from_addr: str):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_addr = from_addr

    def send(self, to_addr: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._from_addr
        message["To"] = to_addr
        message.set_content(body)

        try:
            with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
                smtp.starttls()
                smtp.login(self._username, self._password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailError(f"Failed to send notification email: {exc}") from exc
