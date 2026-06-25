from pathlib import Path

import pytest

from stratos_quant.config import ConfigError, load_settings


def test_load_settings_raises_if_required_fields_missing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    with pytest.raises(ConfigError, match="Missing required environment variable"):
        load_settings(env_file=env_file)


def test_load_settings_reads_expected_values(tmp_path, monkeypatch):
    db_file = tmp_path / "portfolio_management.sqlite3"
    db_file.write_text("", encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"SQLITE_DB_PATH={db_file}",
                "OLLAMA_MODEL=llama3",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    settings = load_settings(env_file=env_file)

    assert settings.sqlite_db_path == Path(db_file)
    assert settings.ollama_model == "llama3"


def test_load_settings_raises_if_db_file_does_not_exist(tmp_path, monkeypatch):
    missing_db_path = tmp_path / "missing.sqlite3"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"SQLITE_DB_PATH={missing_db_path}\nOLLAMA_MODEL=phi3\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    with pytest.raises(ConfigError, match="SQLITE_DB_PATH does not exist"):
        load_settings(env_file=env_file)


def test_load_settings_raises_if_db_path_is_not_a_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"SQLITE_DB_PATH={tmp_path}\nOLLAMA_MODEL=mistral\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    with pytest.raises(ConfigError, match="SQLITE_DB_PATH must point to a file"):
        load_settings(env_file=env_file)
