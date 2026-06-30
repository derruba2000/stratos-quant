from pathlib import Path

import pytest

from stratos_quant.config import ConfigError, load_settings


def test_load_settings_raises_if_required_fields_missing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("API_USAGE", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_API_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_CA_BUNDLE", raising=False)
    monkeypatch.delenv("NVIDIA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("NVIDIA_TIMEOUT_SECONDS", raising=False)

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
    monkeypatch.delenv("API_USAGE", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_API_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_CA_BUNDLE", raising=False)
    monkeypatch.delenv("NVIDIA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("NVIDIA_TIMEOUT_SECONDS", raising=False)

    settings = load_settings(env_file=env_file)

    assert settings.sqlite_db_path == Path(db_file)
    assert settings.api_usage == "OLLAMA"
    assert settings.ollama_model == "llama3"
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.ollama_timeout_seconds == 450
    assert settings.llm_model == "llama3"


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
    monkeypatch.delenv("API_USAGE", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_API_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_CA_BUNDLE", raising=False)
    monkeypatch.delenv("NVIDIA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("NVIDIA_TIMEOUT_SECONDS", raising=False)

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
    monkeypatch.delenv("API_USAGE", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_API_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_CA_BUNDLE", raising=False)
    monkeypatch.delenv("NVIDIA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("NVIDIA_TIMEOUT_SECONDS", raising=False)

    with pytest.raises(ConfigError, match="SQLITE_DB_PATH must point to a file"):
        load_settings(env_file=env_file)


def test_load_settings_reads_nvidia_provider_values(tmp_path, monkeypatch):
    db_file = tmp_path / "portfolio_management.sqlite3"
    db_file.write_text("", encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"SQLITE_DB_PATH={db_file}",
                "API_USAGE=NVIDIA",
                "NVIDIA_API_MODEL=nvidia/llama-3.1-nemotron",
                "NVIDIA_API_KEY=test-key",
                "NVIDIA_TIMEOUT_SECONDS=900",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("API_USAGE", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_API_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_CA_BUNDLE", raising=False)
    monkeypatch.delenv("NVIDIA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("NVIDIA_TIMEOUT_SECONDS", raising=False)

    settings = load_settings(env_file=env_file)

    assert settings.api_usage == "NVIDIA"
    assert settings.nvidia_api_model == "nvidia/llama-3.1-nemotron"
    assert settings.nvidia_api_key == "test-key"
    assert settings.nvidia_ca_bundle is None
    assert settings.nvidia_verify_ssl is True
    assert settings.nvidia_timeout_seconds == 900
    assert settings.llm_model == "nvidia/llama-3.1-nemotron"
    assert settings.llm_provider_label == "NVIDIA"


def test_load_settings_requires_nvidia_values_when_provider_selected(
    tmp_path,
    monkeypatch,
):
    db_file = tmp_path / "portfolio_management.sqlite3"
    db_file.write_text("", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"SQLITE_DB_PATH={db_file}\nAPI_USAGE=NVIDIA\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("API_USAGE", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_API_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_CA_BUNDLE", raising=False)
    monkeypatch.delenv("NVIDIA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("NVIDIA_TIMEOUT_SECONDS", raising=False)

    with pytest.raises(ConfigError, match="NVIDIA_API_MODEL, NVIDIA_API_KEY"):
        load_settings(env_file=env_file)


def test_load_settings_reads_nvidia_ca_bundle(tmp_path, monkeypatch):
    db_file = tmp_path / "portfolio_management.sqlite3"
    db_file.write_text("", encoding="utf-8")
    ca_bundle = tmp_path / "corporate-ca.pem"
    ca_bundle.write_text("certificate", encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"SQLITE_DB_PATH={db_file}",
                "API_USAGE=NVIDIA",
                "NVIDIA_API_MODEL=nvidia/llama-3.1-nemotron",
                "NVIDIA_API_KEY=test-key",
                f"NVIDIA_CA_BUNDLE={ca_bundle}",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("API_USAGE", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_API_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_CA_BUNDLE", raising=False)
    monkeypatch.delenv("NVIDIA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("NVIDIA_TIMEOUT_SECONDS", raising=False)

    settings = load_settings(env_file=env_file)

    assert settings.nvidia_ca_bundle == ca_bundle


def test_load_settings_defaults_nvidia_timeout_to_ollama_timeout(
    tmp_path,
    monkeypatch,
):
    db_file = tmp_path / "portfolio_management.sqlite3"
    db_file.write_text("", encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"SQLITE_DB_PATH={db_file}",
                "API_USAGE=NVIDIA",
                "NVIDIA_API_MODEL=nvidia/llama-3.1-nemotron",
                "NVIDIA_API_KEY=test-key",
                "OLLAMA_TIMEOUT_SECONDS=450",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("API_USAGE", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_API_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_CA_BUNDLE", raising=False)
    monkeypatch.delenv("NVIDIA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("NVIDIA_TIMEOUT_SECONDS", raising=False)

    settings = load_settings(env_file=env_file)

    assert settings.nvidia_timeout_seconds == 450


def test_load_settings_can_disable_nvidia_ssl_verification(tmp_path, monkeypatch):
    db_file = tmp_path / "portfolio_management.sqlite3"
    db_file.write_text("", encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"SQLITE_DB_PATH={db_file}",
                "API_USAGE=NVIDIA",
                "NVIDIA_API_MODEL=nvidia/llama-3.1-nemotron",
                "NVIDIA_API_KEY=test-key",
                "NVIDIA_VERIFY_SSL=false",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("API_USAGE", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_API_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_CA_BUNDLE", raising=False)
    monkeypatch.delenv("NVIDIA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("NVIDIA_TIMEOUT_SECONDS", raising=False)

    settings = load_settings(env_file=env_file)

    assert settings.nvidia_verify_ssl is False


def test_load_settings_rejects_invalid_nvidia_ssl_verification(tmp_path, monkeypatch):
    db_file = tmp_path / "portfolio_management.sqlite3"
    db_file.write_text("", encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"SQLITE_DB_PATH={db_file}",
                "API_USAGE=NVIDIA",
                "NVIDIA_API_MODEL=nvidia/llama-3.1-nemotron",
                "NVIDIA_API_KEY=test-key",
                "NVIDIA_VERIFY_SSL=maybe",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("API_USAGE", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_API_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_CA_BUNDLE", raising=False)
    monkeypatch.delenv("NVIDIA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("NVIDIA_TIMEOUT_SECONDS", raising=False)

    with pytest.raises(ConfigError, match="NVIDIA_VERIFY_SSL"):
        load_settings(env_file=env_file)


def test_load_settings_rejects_missing_nvidia_ca_bundle(tmp_path, monkeypatch):
    db_file = tmp_path / "portfolio_management.sqlite3"
    db_file.write_text("", encoding="utf-8")
    missing_ca = tmp_path / "missing-ca.pem"

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"SQLITE_DB_PATH={db_file}",
                "API_USAGE=NVIDIA",
                "NVIDIA_API_MODEL=nvidia/llama-3.1-nemotron",
                "NVIDIA_API_KEY=test-key",
                f"NVIDIA_CA_BUNDLE={missing_ca}",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.delenv("API_USAGE", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_API_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_CA_BUNDLE", raising=False)
    monkeypatch.delenv("NVIDIA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("NVIDIA_TIMEOUT_SECONDS", raising=False)

    with pytest.raises(ConfigError, match="NVIDIA_CA_BUNDLE does not exist"):
        load_settings(env_file=env_file)
