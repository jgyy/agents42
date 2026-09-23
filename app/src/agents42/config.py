from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://agents42:agents42@localhost:5432/agents42"
    businesses_dir: Path = Path("businesses")
    migrations_dir: Path = Path("migrations")

    google_calendar_credentials_path: Path = Path("credentials/calendar_credentials.json")
    google_calendar_token_path: Path = Path("credentials/calendar_token.json")

    # Owner dashboard (owner_api.py). Defaulted empty rather than required -
    # this Settings class is shared with api.py/tests, which must keep
    # working with no dashboard configured at all. owner_api.py's own
    # lifespan fails fast if either is unset instead.
    owner_dashboard_password: str = ""
    owner_dashboard_business_id: str = ""

    # Owner escalation email notifications (integrations/email_notifier.py).
    # All optional/defaulted empty - notifications are a best-effort side
    # effect of creating an escalation, never a requirement for it to
    # succeed. get_email_notifier() returns None if smtp_host is unset,
    # which create_escalation treats as "notifications not configured".
    owner_notification_email: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""


settings = Settings()
