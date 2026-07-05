from __future__ import annotations

import json
import logging
import re
import ssl
import time
from typing import Any, Mapping, Protocol

import httpx
from openai import OpenAI, OpenAIError
import requests

from stratos_quant.config import AppConfig, load_settings

from .errors import OllamaError, OllamaResponseError

logger = logging.getLogger(__name__)


def _response_excerpt(response: requests.Response | None) -> str:
    if response is None:
        return ""
    text = getattr(response, "text", "") or ""
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) > 500:
        compact = f"{compact[:500]}..."
    return f" Response body: {compact}"


def _content_excerpt(content: str) -> str:
    compact = " ".join(content.split())
    if len(compact) > 500:
        compact = f"{compact[:500]}..."
    return compact


def _loads_json_object(content: str, *, provider: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = _loads_embedded_json(content)
    if not isinstance(parsed, dict):
        raise OllamaResponseError(f"{provider} JSON response must be an object")
    return parsed


def _loads_embedded_json(content: str) -> Any:
    stripped = content.strip()
    fence_match = re.search(
        r"```(?:json)?\s*(?P<body>.*?)\s*```",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fence_match is not None:
        return json.loads(fence_match.group("body"))

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise json.JSONDecodeError("No JSON object found", stripped, 0)
    return json.loads(stripped[start : end + 1])


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
    ) -> str | None:
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

        for attempt in range(4):
            try:
                response = self._session.post(
                    f"{self.settings.ollama_base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                body = response.json()

                content = body.get("message", {}).get("content")
                if not isinstance(content, str) or not content.strip():
                    raise OllamaResponseError("Ollama returned an empty chat response")
                return content.strip()

            except requests.HTTPError as exc:
                if (
                    exc.response is not None
                    and exc.response.status_code == 429
                ):
                    if attempt < 3:
                        delay = 2 ** (attempt + 1)
                        logger.warning(
                            f"Ollama 429 error (Too Many Requests). "
                            f"Retrying in {delay}s... (Attempt {attempt+1}/3)"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error("Ollama max retries reached for 429 error.")
                        return None
                raise OllamaError(
                    f"Ollama request failed for model {self.settings.ollama_model}: "
                    f"{exc}.{_response_excerpt(exc.response)}"
                ) from exc
            except (requests.RequestException, ValueError) as exc:
                raise OllamaError(
                    f"Ollama request failed for model {self.settings.ollama_model}: {exc}"
                ) from exc

        return None

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        content = self.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
        )
        if content is None:
            return None
        try:
            return _loads_json_object(content, provider="Ollama")
        except json.JSONDecodeError as exc:
            raise OllamaResponseError(
                "Ollama returned invalid JSON. "
                f"Response excerpt: {_content_excerpt(content)}"
            ) from exc


class ChatClient(Protocol):
    settings: AppConfig

    def chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any] | None = None,
    ) -> str | None: ...

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any],
    ) -> dict[str, Any] | None: ...


class NvidiaClient:
    """Client for NVIDIA's OpenAI-compatible chat completions API."""

    base_url = "https://integrate.api.nvidia.com/v1"

    def __init__(
        self,
        settings: AppConfig | None = None,
        *,
        session: requests.Session | None = None,
        openai_client: Any | None = None,
        http_client: httpx.Client | None = None,
        timeout: float | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.timeout = (
            timeout
            if timeout is not None
            else self.settings.nvidia_timeout_seconds
        )
        self._session = session
        self._http_client = http_client
        self._client = openai_client or self._create_client()

    def _create_client(self) -> OpenAI:
        verify: bool | ssl.SSLContext
        if not self.settings.nvidia_verify_ssl:
            verify = False
        else:
            verify = ssl.create_default_context(
                cafile=(
                    str(self.settings.nvidia_ca_bundle)
                    if self.settings.nvidia_ca_bundle is not None
                    else requests.certs.where()
                )
            )
        self._http_client = self._http_client or httpx.Client(
            verify=verify,
            timeout=self.timeout,
        )
        return OpenAI(
            base_url=self.base_url,
            api_key=self.settings.nvidia_api_key,
            http_client=self._http_client,
            timeout=self.timeout,
        )

    def chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any] | None = None,
    ) -> str | None:
        resolved_user_prompt = user_prompt
        if response_schema is not None:
            resolved_user_prompt = (
                f"{user_prompt}\n\nReturn only valid JSON matching this JSON "
                f"Schema:\n{json.dumps(response_schema, sort_keys=True)}"
            )

        for attempt in range(4):
            try:
                request: dict[str, Any] = {
                    "model": self.settings.nvidia_api_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": resolved_user_prompt},
                    ],
                    "max_tokens": 4096,
                    "temperature": 0,
                }
                if response_schema is not None:
                    request["response_format"] = {"type": "json_object"}
                response = self._client.chat.completions.create(**request)

                choices = getattr(response, "choices", None)
                if not choices:
                    raise OllamaResponseError("NVIDIA returned an empty chat response")
                content = getattr(getattr(choices[0], "message", None), "content", None)
                if not isinstance(content, str) or not content.strip():
                    raise OllamaResponseError("NVIDIA returned an empty chat response")
                return content.strip()

            except OpenAIError as exc:
                if (
                    getattr(exc, "status_code", None) == 429
                    and attempt < 3
                ):
                    delay = 2 ** (attempt + 1)
                    logger.warning(
                        f"NVIDIA 429 error (Too Many Requests). "
                        f"Retrying in {delay}s... (Attempt {attempt+1}/3)"
                    )
                    time.sleep(delay)
                    continue
                elif getattr(exc, "status_code", None) == 429:
                    logger.error("NVIDIA max retries reached for 429 error.")
                    return None

                hint = ""
                if getattr(exc, "status_code", None) == 404:
                    hint = (
                        " Check NVIDIA_API_MODEL; this usually means the model "
                        "slug is not available for NVIDIA chat completions or your "
                        "API key does not have access."
                    )
                raise OllamaError(
                    "NVIDIA request failed for model "
                    f"{self.settings.nvidia_api_model}: {exc}.{hint}"
                ) from exc
            except Exception as exc:
                raise OllamaError(
                    "NVIDIA request failed for model "
                    f"{self.settings.nvidia_api_model}: {exc}"
                ) from exc

        return None

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        content = self.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
        )
        if content is None:
            return None
        try:
            return _loads_json_object(content, provider="NVIDIA")
        except json.JSONDecodeError as exc:
            raise OllamaResponseError(
                "NVIDIA returned invalid JSON. "
                f"Response excerpt: {_content_excerpt(content)}"
            ) from exc


def create_chat_client(
    settings: AppConfig | None = None,
    *,
    session: requests.Session | None = None,
    timeout: float | None = None,
) -> ChatClient:
    resolved_settings = settings or load_settings()
    if resolved_settings.api_usage == "NVIDIA":
        return NvidiaClient(resolved_settings, session=session, timeout=timeout)
    return OllamaClient(resolved_settings, session=session, timeout=timeout)
