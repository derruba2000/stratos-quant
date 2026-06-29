from pathlib import Path

import pytest

from stratos_quant.config import ConfigError, load_settings


def test_load_settings_raises_if_required_fields_missing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

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
                "OLLAMA_BASE_URL=http://localhost:11434",
                "OLLAMA_TIMEOUT_SECONDS=450",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    settings = load_settings(env_file=env_file)

    assert settings.sqlite_db_path == Path(db_file)
    assert settings.ollama_model == "llama3"
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.ollama_timeout_seconds == 450


def test_load_settings_raises_if_db_file_does_not_exist(tmp_path, monkeypatch):
    missing_db_path = tmp_path / "missing.sqlite3"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"SQLITE_DB_PATH={missing_db_path}\n"
        "OLLAMA_MODEL=phi3\n"
        "OLLAMA_BASE_URL=http://localhost:11434\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    with pytest.raises(ConfigError, match="SQLITE_DB_PATH does not exist"):
        load_settings(env_file=env_file)


def test_load_settings_raises_if_db_path_is_not_a_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"SQLITE_DB_PATH={tmp_path}\n"
        "OLLAMA_MODEL=mistral\n"
        "OLLAMA_BASE_URL=http://localhost:11434\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    with pytest.raises(ConfigError, match="SQLITE_DB_PATH must point to a file"):
        load_settings(env_file=env_file)
