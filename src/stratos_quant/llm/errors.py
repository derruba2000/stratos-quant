"""Exceptions raised by the local LLM integration."""


class OllamaError(RuntimeError):
    """Raised when Ollama cannot complete a request."""


class OllamaResponseError(OllamaError):
    """Raised when Ollama returns malformed or invalid structured output."""
