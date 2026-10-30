from collections.abc import Iterator
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from .config import Settings
from .models import Base


def create_database(settings: Settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.thumbnail_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {})
    if settings.database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    # Lightweight additive migration for local Phase 1 databases.
    existing = {column["name"] for column in inspect(engine).get_columns("photos")}
    additions = {
        "technical_score": "FLOAT", "recommendation": "VARCHAR(16)",
        "analysis_json": "TEXT", "analysis_version": "VARCHAR(32)",
        "analysis_source_signature": "VARCHAR(128)",
    }
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE photos ADD COLUMN {name} {sql_type}"))
    return engine, sessionmaker(engine, expire_on_commit=False)


def session_dependency(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        yield session
