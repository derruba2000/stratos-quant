from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class SecurityRecommendation:
    security_id: int
    ticker: str
    action_type: str
    target_weight: Decimal
    rationale: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SecurityRecommendation":
        action = str(value.get("action_type", "")).upper()
        if action not in {"BUY", "SELL", "HOLD"}:
            raise ValueError("action_type must be BUY, SELL, or HOLD")
        target_weight = Decimal(str(value.get("target_weight")))
        if not Decimal("0") <= target_weight <= Decimal("1"):
            raise ValueError("target_weight must be between 0 and 1")
        rationale = str(value.get("rationale", "")).strip()
        ticker = str(value.get("ticker", "")).strip()
        if not ticker or not rationale:
            raise ValueError("ticker and rationale are required")
        return cls(
            security_id=int(value["security_id"]),
            ticker=ticker,
            action_type=action,
            target_weight=target_weight,
            rationale=rationale,
        )
