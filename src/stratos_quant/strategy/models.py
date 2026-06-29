from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_DOWN
from typing import Any, Mapping


WEIGHT_PRECISION = Decimal("0.0000000001")
ONE = Decimal("1.0000000000")
ZERO = Decimal("0.0000000000")


def normalize_weights(weights: Mapping[str, float | Decimal]) -> dict[str, Decimal]:
    """Normalize non-negative weights to exactly 1.0000000000."""
    cleaned = {
        code: Decimal(str(value))
        for code, value in weights.items()
        if Decimal(str(value)) > 0
    }
    total = sum(cleaned.values(), Decimal("0"))
    if total <= 0:
        raise ValueError("At least one positive allocation weight is required")

    ordered_codes = sorted(cleaned)
    normalized: dict[str, Decimal] = {}
    allocated = Decimal("0")
    for code in ordered_codes[:-1]:
        weight = (cleaned[code] / total).quantize(
            WEIGHT_PRECISION,
            rounding=ROUND_DOWN,
        )
        normalized[code] = weight
        allocated += weight
    normalized[ordered_codes[-1]] = (ONE - allocated).quantize(WEIGHT_PRECISION)
    return normalized


@dataclass(frozen=True, slots=True)
class AssetClassSignal:
    asset_class_code: str
    trend_positive: bool
    momentum_12m: float | None
    annualized_volatility: float | None
    security_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_class_code": self.asset_class_code,
            "trend_positive": self.trend_positive,
            "momentum_12m": self.momentum_12m,
            "annualized_volatility": self.annualized_volatility,
            "security_count": self.security_count,
        }


@dataclass(frozen=True, slots=True)
class SecuritySignal:
    security_id: int
    ticker: str
    asset_class_code: str
    trend_positive: bool
    momentum_12m: float
    annualized_volatility: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "ticker": self.ticker,
            "asset_class_code": self.asset_class_code,
            "trend_positive": self.trend_positive,
            "momentum_12m": self.momentum_12m,
            "annualized_volatility": self.annualized_volatility,
        }


@dataclass(frozen=True, slots=True)
class AllocationResult:
    model: str
    as_of: date
    weights: Mapping[str, Decimal]
    signals: tuple[AssetClassSignal, ...]
    component_weights: Mapping[str, Mapping[str, Decimal]] | None = None
    security_signals: tuple[SecuritySignal, ...] = ()

    def __post_init__(self) -> None:
        if sum(self.weights.values(), Decimal("0")) != ONE:
            raise ValueError("Allocation weights must sum to exactly 1.0000000000")

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "as_of": self.as_of.isoformat(),
            "weights": {
                code: format(weight, ".10f")
                for code, weight in sorted(self.weights.items())
            },
            "signals": [signal.to_dict() for signal in self.signals],
            "security_signals": [
                signal.to_dict() for signal in self.security_signals
            ],
            "component_weights": (
                {
                    component: {
                        code: format(weight, ".10f")
                        for code, weight in sorted(weights.items())
                    }
                    for component, weights in self.component_weights.items()
                }
                if self.component_weights is not None
                else None
            ),
        }
