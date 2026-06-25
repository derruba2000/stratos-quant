"""Exceptions raised by quantitative allocation engines."""


class StrategyError(RuntimeError):
    """Base exception for deterministic strategy failures."""


class InsufficientPriceHistoryError(StrategyError):
    """Raised when no security has enough observations for an allocation."""
