from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://agents42:agents42@localhost:5432/agents42"
    businesses_dir: Path = Path("businesses")
    migrations_dir: Path = Path("migrations")

    google_calendar_credentials_path: Path = Path("credentials/calendar_credentials.json")
    google_calendar_token_path: Path = Path("credentials/calendar_token.json")


settings = Settings()
