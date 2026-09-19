from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from agents42.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def run_migrations() -> None:
    """Apply any .sql files under migrations/ that have not been applied yet.

    Deliberately not Alembic: for a hackathon-sized schema, a tracked list of
    plain SQL files is easier to read and debug than a migration framework.

    settings.migrations_dir is resolved relative to the process's cwd (like
    settings.businesses_dir) rather than this file's location, so it lands on
    the right directory in both contexts: Docker's WORKDIR is /app with
    migrations/ copied directly under it, and local (non-Docker) runs are
    expected to run from the repo root, where migrations/ is a direct child.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
        )
        applied = {row[0] for row in conn.execute(text("SELECT filename FROM schema_migrations"))}

        for path in sorted(settings.migrations_dir.glob("*.sql")):
            if path.name in applied:
                continue
            conn.execute(text(path.read_text()))
            conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:filename)"),
                {"filename": path.name},
            )


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
