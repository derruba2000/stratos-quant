from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when required environment settings are missing or invalid."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    sqlite_db_path: Path
    ollama_model: str
    ollama_base_url: str
    api_usage: str = "OLLAMA"
    ollama_timeout_seconds: float = 300.0
    nvidia_api_model: str = ""
    nvidia_api_key: str = ""
    nvidia_ca_bundle: Path | None = None
    nvidia_verify_ssl: bool = True
    nvidia_timeout_seconds: float = 300.0

    @property
    def llm_model(self) -> str:
        """Return the configured model for the active LLM provider."""
        if self.api_usage == "NVIDIA":
            return self.nvidia_api_model
        return self.ollama_model

    @property
    def llm_provider_label(self) -> str:
        if self.api_usage == "NVIDIA":
            return "NVIDIA"
        return "Ollama"


def load_settings(env_file: str | Path = ".env") -> AppConfig:
    """Load and validate runtime settings from environment variables."""
    load_dotenv(dotenv_path=env_file, override=False)

    sqlite_db_path = (os.getenv("SQLITE_DB_PATH") or "").strip()
    api_usage = (os.getenv("API_USAGE") or "ollama").strip().upper()
    ollama_model = (os.getenv("OLLAMA_MODEL") or "").strip()
    ollama_base_url = (os.getenv("OLLAMA_BASE_URL") or "").strip()
    ollama_timeout_value = (
        os.getenv("OLLAMA_TIMEOUT_SECONDS") or "300"
    ).strip()
    nvidia_api_model = (os.getenv("NVIDIA_API_MODEL") or "").strip()
    nvidia_api_key = (os.getenv("NVIDIA_API_KEY") or "").strip()
    nvidia_ca_bundle_value = (os.getenv("NVIDIA_CA_BUNDLE") or "").strip()
    nvidia_verify_ssl_value = (
        os.getenv("NVIDIA_VERIFY_SSL") or "true"
    ).strip().lower()
    nvidia_timeout_value = (
        os.getenv("NVIDIA_TIMEOUT_SECONDS") or ollama_timeout_value
    ).strip()
    if api_usage not in {"OLLAMA", "NVIDIA"}:
        raise ConfigError("API_USAGE must be either OLLAMA or NVIDIA")
    if nvidia_verify_ssl_value not in {"true", "false"}:
        raise ConfigError("NVIDIA_VERIFY_SSL must be either true or false")
    nvidia_verify_ssl = nvidia_verify_ssl_value == "true"

    required_values = {"SQLITE_DB_PATH": sqlite_db_path}
    if api_usage == "NVIDIA":
        required_values.update(
            {
                "NVIDIA_API_MODEL": nvidia_api_model,
                "NVIDIA_API_KEY": nvidia_api_key,
            }
        )
    else:
        required_values.update(
            {
                "OLLAMA_MODEL": ollama_model,
                "OLLAMA_BASE_URL": ollama_base_url,
            }
        )
    missing_keys = [
        key
        for key, value in required_values.items()
        if not value
    ]
    if missing_keys:
        joined = ", ".join(missing_keys)
        raise ConfigError(f"Missing required environment variable(s): {joined}")

    db_path = Path(sqlite_db_path).expanduser()
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()

    if not db_path.exists():
        raise ConfigError(
            f"SQLITE_DB_PATH does not exist: {db_path}"
        )
    if not db_path.is_file():
        raise ConfigError(
            f"SQLITE_DB_PATH must point to a file: {db_path}"
        )

    nvidia_ca_bundle = None
    if nvidia_ca_bundle_value:
        nvidia_ca_bundle = Path(nvidia_ca_bundle_value).expanduser()
        if not nvidia_ca_bundle.is_absolute():
            nvidia_ca_bundle = (Path.cwd() / nvidia_ca_bundle).resolve()
        if not nvidia_ca_bundle.exists():
            raise ConfigError(
                f"NVIDIA_CA_BUNDLE does not exist: {nvidia_ca_bundle}"
            )
        if not nvidia_ca_bundle.is_file():
            raise ConfigError(
                f"NVIDIA_CA_BUNDLE must point to a file: {nvidia_ca_bundle}"
            )

    if api_usage == "OLLAMA" and not ollama_base_url.startswith(
        ("http://", "https://")
    ):
        raise ConfigError("OLLAMA_BASE_URL must be an http:// or https:// URL")
    try:
        ollama_timeout_seconds = float(ollama_timeout_value)
    except ValueError as exc:
        raise ConfigError("OLLAMA_TIMEOUT_SECONDS must be a number") from exc
    if ollama_timeout_seconds <= 0:
        raise ConfigError("OLLAMA_TIMEOUT_SECONDS must be greater than zero")
    try:
        nvidia_timeout_seconds = float(nvidia_timeout_value)
    except ValueError as exc:
        raise ConfigError("NVIDIA_TIMEOUT_SECONDS must be a number") from exc
    if nvidia_timeout_seconds <= 0:
        raise ConfigError("NVIDIA_TIMEOUT_SECONDS must be greater than zero")
    return AppConfig(
        sqlite_db_path=db_path,
        api_usage=api_usage,
        ollama_model=ollama_model,
        ollama_base_url=ollama_base_url.rstrip("/"),
        ollama_timeout_seconds=ollama_timeout_seconds,
        nvidia_api_model=nvidia_api_model,
        nvidia_api_key=nvidia_api_key,
        nvidia_ca_bundle=nvidia_ca_bundle,
        nvidia_verify_ssl=nvidia_verify_ssl,
        nvidia_timeout_seconds=nvidia_timeout_seconds,
    )
