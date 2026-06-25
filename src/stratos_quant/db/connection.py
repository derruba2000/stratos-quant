from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL

from stratos_quant.config import AppConfig, load_settings


def create_sqlite_engine(settings: AppConfig | None = None) -> Engine:
    """Build a SQLAlchemy engine from app settings."""
    resolved_settings = settings or load_settings()
    db_url = URL.create(
        drivername="sqlite",
        database=str(resolved_settings.sqlite_db_path),
    )
    return create_engine(db_url, future=True)
