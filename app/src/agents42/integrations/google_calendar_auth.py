"""One-time local setup: run this on a machine with a browser to produce the
refresh token the headless app/AWS deployment will use.

    python -m agents42.integrations.google_calendar_auth

See DEVELOPMENT.md "Google Calendar setup" for the full walkthrough
(creating the OAuth client, sharing the calendar, etc).
"""

from google_auth_oauthlib.flow import InstalledAppFlow

from agents42.config import settings
from agents42.integrations.google_calendar import SCOPES


def main() -> None:
    if not settings.google_calendar_credentials_path.exists():
        raise SystemExit(
            f"Missing OAuth client secret at {settings.google_calendar_credentials_path}. "
            "Download it from Google Cloud Console (APIs & Services > Credentials) first."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(settings.google_calendar_credentials_path), SCOPES)
    creds = flow.run_local_server(port=0)

    settings.google_calendar_token_path.parent.mkdir(parents=True, exist_ok=True)
    settings.google_calendar_token_path.write_text(creds.to_json())
    print(f"Saved refresh token to {settings.google_calendar_token_path}")


if __name__ == "__main__":
    main()
