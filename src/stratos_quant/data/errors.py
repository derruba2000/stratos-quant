"""Exceptions raised by data extraction services."""


class DataExtractionError(RuntimeError):
    """Raised when source data cannot be transformed safely."""


class MissingMarketDataError(DataExtractionError):
    """Raised when a held security cannot be valued from available market data."""
