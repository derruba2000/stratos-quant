from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class DriftBand:
    min_drift: Decimal
    max_drift: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_drift": self.min_drift,
            "max_drift": self.max_drift,
        }


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
    allowed_min: Decimal | None = None
    allowed_max: Decimal | None = None
    allocation_drift_weight: Decimal | None = None
    rebalance_weight: Decimal | None = None
    rebalance_required: bool = False

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
            "allowed_min": self.allowed_min,
            "allowed_max": self.allowed_max,
            "allocation_drift_weight": self.allocation_drift_weight,
            "rebalance_weight": self.rebalance_weight,
            "rebalance_required": self.rebalance_required,
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
    current_weight: Decimal | None = None
    allowed_min: Decimal | None = None
    allowed_max: Decimal | None = None
    rebalance_weight: Decimal | None = None
    current_value: Decimal | None = None
    target_value: Decimal | None = None
    estimated_quantity: Decimal | None = None
    estimated_fees: Decimal = Decimal("0")
    estimated_slippage: Decimal = Decimal("0")
    estimated_tax_cost: Decimal = Decimal("0")
    expected_benefit: Decimal = Decimal("0")
    net_expected_benefit: Decimal = Decimal("0")
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "ticker": self.ticker,
            "asset_class_code": self.asset_class_code,
            "action_type": self.action_type,
            "target_weight": self.target_weight,
            "estimated_trade_value": self.estimated_trade_value,
            "rationale": self.rationale,
            "current_weight": self.current_weight,
            "allowed_min": self.allowed_min,
            "allowed_max": self.allowed_max,
            "rebalance_weight": self.rebalance_weight,
            "current_value": self.current_value,
            "target_value": self.target_value,
            "estimated_quantity": self.estimated_quantity,
            "estimated_fees": self.estimated_fees,
            "estimated_slippage": self.estimated_slippage,
            "estimated_tax_cost": self.estimated_tax_cost,
            "expected_benefit": self.expected_benefit,
            "net_expected_benefit": self.net_expected_benefit,
            "skipped_reason": self.skipped_reason,
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
    skipped_mandates: tuple[RebalanceMandate, ...] = ()
    portfolio_drift: Decimal = Decimal("0")
    rebalance_required: bool = False
    trigger_reasons: tuple[str, ...] = ()
    schedule: str = "MANUAL"
    expected_benefit: Decimal = Decimal("0")
    estimated_fees: Decimal = Decimal("0")
    estimated_slippage: Decimal = Decimal("0")
    estimated_tax_cost: Decimal = Decimal("0")
    net_expected_benefit: Decimal = Decimal("0")
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "portfolio_id": self.portfolio_id,
            "currency": self.currency,
            "portfolio_value": self.portfolio_value,
            "drift_threshold": self.drift_threshold,
            "drifts": [drift.to_dict() for drift in self.drifts],
            "mandates": [mandate.to_dict() for mandate in self.mandates],
            "skipped_mandates": [
                mandate.to_dict() for mandate in self.skipped_mandates
            ],
            "portfolio_drift": self.portfolio_drift,
            "rebalance_required": self.rebalance_required,
            "trigger_reasons": list(self.trigger_reasons),
            "schedule": self.schedule,
            "expected_benefit": self.expected_benefit,
            "estimated_fees": self.estimated_fees,
            "estimated_slippage": self.estimated_slippage,
            "estimated_tax_cost": self.estimated_tax_cost,
            "net_expected_benefit": self.net_expected_benefit,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class RebalanceScheduleDecision:
    schedule: str
    as_of: date
    due: bool
    trigger_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule": self.schedule,
            "as_of": self.as_of.isoformat(),
            "due": self.due,
            "trigger_reasons": list(self.trigger_reasons),
        }
