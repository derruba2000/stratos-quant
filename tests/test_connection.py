from pathlib import Path

from sqlalchemy import text

from stratos_quant.config import AppConfig
from stratos_quant.db import create_sqlite_engine


def test_create_sqlite_engine_connects_to_database(tmp_path):
    db_file = tmp_path / "portfolio_management.sqlite3"
    db_file.write_text("", encoding="utf-8")

    settings = AppConfig(
        sqlite_db_path=Path(db_file),
        ollama_model="mistral",
        ollama_base_url="http://localhost:11434",
    )
    engine = create_sqlite_engine(settings=settings)

    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1")).scalar_one()

    assert result == 1


def test_create_sqlite_engine_handles_special_characters_in_path(tmp_path):
    db_file = tmp_path / "portfolio #1?.sqlite3"
    db_file.write_text("", encoding="utf-8")

    settings = AppConfig(
        sqlite_db_path=db_file,
        ollama_model="phi3",
        ollama_base_url="http://localhost:11434",
    )
    engine = create_sqlite_engine(settings=settings)

    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1")).scalar_one()

    assert result == 1
