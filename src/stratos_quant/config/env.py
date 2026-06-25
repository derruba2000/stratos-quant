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


def load_settings(env_file: str | Path = ".env") -> AppConfig:
    """Load and validate runtime settings from environment variables."""
    load_dotenv(dotenv_path=env_file, override=False)

    sqlite_db_path = (os.getenv("SQLITE_DB_PATH") or "").strip()
    ollama_model = (os.getenv("OLLAMA_MODEL") or "").strip()
    ollama_base_url = (os.getenv("OLLAMA_BASE_URL") or "").strip()

    missing_keys = [
        key
        for key, value in {
            "SQLITE_DB_PATH": sqlite_db_path,
            "OLLAMA_MODEL": ollama_model,
            "OLLAMA_BASE_URL": ollama_base_url,
        }.items()
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

    if not ollama_base_url.startswith(("http://", "https://")):
        raise ConfigError("OLLAMA_BASE_URL must be an http:// or https:// URL")

    return AppConfig(
        sqlite_db_path=db_path,
        ollama_model=ollama_model,
        ollama_base_url=ollama_base_url.rstrip("/"),
    )
