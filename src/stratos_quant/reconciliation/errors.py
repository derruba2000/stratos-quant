"""Exceptions raised by the reconciliation layer."""


class ReconciliationError(RuntimeError):
    """Raised when portfolio targets cannot be reconciled safely."""
