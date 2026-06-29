from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class AssetClassDrift:
    asset_class_code: str
    current_value: Decimal
    current_weight: Decimal
    target_value: Decimal
    target_weight: Decimal
    drift_value: Decimal
    drift_weight: Decimal
    suppressed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_class_code": self.asset_class_code,
            "current_value": self.current_value,
            "current_weight": self.current_weight,
            "target_value": self.target_value,
            "target_weight": self.target_weight,
            "drift_value": self.drift_value,
            "drift_weight": self.drift_weight,
            "suppressed": self.suppressed,
        }


@dataclass(frozen=True, slots=True)
class RebalanceMandate:
    security_id: int
    ticker: str
    asset_class_code: str
    action_type: str
    target_weight: Decimal
    estimated_trade_value: Decimal
    rationale: str
    recommendation_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "ticker": self.ticker,
            "asset_class_code": self.asset_class_code,
            "action_type": self.action_type,
            "target_weight": self.target_weight,
            "estimated_trade_value": self.estimated_trade_value,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    run_id: int
    portfolio_id: int
    currency: str
    portfolio_value: Decimal
    drift_threshold: Decimal
    drifts: tuple[AssetClassDrift, ...]
    mandates: tuple[RebalanceMandate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "portfolio_id": self.portfolio_id,
            "currency": self.currency,
            "portfolio_value": self.portfolio_value,
            "drift_threshold": self.drift_threshold,
            "drifts": [drift.to_dict() for drift in self.drifts],
            "mandates": [mandate.to_dict() for mandate in self.mandates],
        }
