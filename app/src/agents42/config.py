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


settings = Settings()
