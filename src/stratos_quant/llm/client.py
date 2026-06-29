from __future__ import annotations

import json
from typing import Any, Mapping

import requests

from stratos_quant.config import AppConfig, load_settings

from .errors import OllamaError, OllamaResponseError


class OllamaClient:
    """Small, testable client for Ollama's local chat API."""

    def __init__(
        self,
        settings: AppConfig | None = None,
        *,
        session: requests.Session | None = None,
        timeout: float | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self._session = session or requests.Session()
        self.timeout = (
            timeout
            if timeout is not None
            else self.settings.ollama_timeout_seconds
        )

    def chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.settings.ollama_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if response_schema is not None:
            payload["format"] = response_schema
            payload["options"] = {"temperature": 0}

        try:
            response = self._session.post(
                f"{self.settings.ollama_base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise OllamaError(
                f"Ollama request failed for model {self.settings.ollama_model}: {exc}"
            ) from exc

        content = body.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise OllamaResponseError("Ollama returned an empty chat response")
        return content.strip()

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any],
    ) -> dict[str, Any]:
        content = self.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
        )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OllamaResponseError("Ollama returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise OllamaResponseError("Ollama JSON response must be an object")
        return parsed
