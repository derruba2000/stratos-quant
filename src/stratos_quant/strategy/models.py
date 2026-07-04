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
    momentum_24m: float | None = None
    momentum_36m: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    beta_vs_benchmark: float | None = None
    alpha: float | None = None
    macd: float | None = None
    time_weighted_return: float | None = None
    cagr: float | None = None
    sortino_ratio: float | None = None
    treynor_ratio: float | None = None
    yield_: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_class_code": self.asset_class_code,
            "trend_positive": self.trend_positive,
            "momentum_12m": self.momentum_12m,
            "momentum_24m": self.momentum_24m,
            "momentum_36m": self.momentum_36m,
            "annualized_volatility": self.annualized_volatility,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "beta_vs_benchmark": self.beta_vs_benchmark,
            "alpha": self.alpha,
            "macd": self.macd,
            "time_weighted_return": self.time_weighted_return,
            "cagr": self.cagr,
            "sortino_ratio": self.sortino_ratio,
            "treynor_ratio": self.treynor_ratio,
            "yield": self.yield_,
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
    momentum_24m: float | None = None
    momentum_36m: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    beta_vs_benchmark: float | None = None
    alpha: float | None = None
    macd: float | None = None
    time_weighted_return: float | None = None
    cagr: float | None = None
    sortino_ratio: float | None = None
    treynor_ratio: float | None = None
    yield_: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "ticker": self.ticker,
            "asset_class_code": self.asset_class_code,
            "trend_positive": self.trend_positive,
            "momentum_12m": self.momentum_12m,
            "momentum_24m": self.momentum_24m,
            "momentum_36m": self.momentum_36m,
            "annualized_volatility": self.annualized_volatility,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "beta_vs_benchmark": self.beta_vs_benchmark,
            "alpha": self.alpha,
            "macd": self.macd,
            "time_weighted_return": self.time_weighted_return,
            "cagr": self.cagr,
            "sortino_ratio": self.sortino_ratio,
            "treynor_ratio": self.treynor_ratio,
            "yield": self.yield_,
        }


@dataclass(frozen=True, slots=True)
class MomentumSignal:
    security_id: int
    ticker: str
    asset_class_code: str
    as_of: date
    returns: Mapping[str, float | None]
    momentum_score: float
    momentum_signal: str
    confidence_score: float
    relative_strength_vs_benchmark: float | None
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "ticker": self.ticker,
            "asset_class_code": self.asset_class_code,
            "as_of": self.as_of.isoformat(),
            "returns": dict(self.returns),
            "momentum_score": self.momentum_score,
            "momentum_signal": self.momentum_signal,
            "confidence_score": self.confidence_score,
            "relative_strength_vs_benchmark": self.relative_strength_vs_benchmark,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class TrendSignal:
    security_id: int
    ticker: str
    asset_class_code: str
    as_of: date
    close: float
    moving_average_50d: float | None
    moving_average_200d: float | None
    recent_return_1m: float | None
    drawdown: float | None
    drawdown_worsening: bool
    trend_signal: str
    explanation: str

    @property
    def trend_positive(self) -> bool:
        return self.trend_signal == "bullish"

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "ticker": self.ticker,
            "asset_class_code": self.asset_class_code,
            "as_of": self.as_of.isoformat(),
            "close": self.close,
            "moving_average_50d": self.moving_average_50d,
            "moving_average_200d": self.moving_average_200d,
            "recent_return_1m": self.recent_return_1m,
            "drawdown": self.drawdown,
            "drawdown_worsening": self.drawdown_worsening,
            "trend_signal": self.trend_signal,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class VolatilitySignal:
    security_id: int
    ticker: str
    asset_class_code: str
    as_of: date
    rolling_20d_volatility: float | None
    rolling_60d_volatility: float | None
    rolling_252d_volatility: float | None
    downside_volatility: float | None
    volatility_percentile: float | None
    volatility_signal: str
    ranking_score: float
    target_weight_multiplier: float
    risk_cap: float | None
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "ticker": self.ticker,
            "asset_class_code": self.asset_class_code,
            "as_of": self.as_of.isoformat(),
            "rolling_20d_volatility": self.rolling_20d_volatility,
            "rolling_60d_volatility": self.rolling_60d_volatility,
            "rolling_252d_volatility": self.rolling_252d_volatility,
            "downside_volatility": self.downside_volatility,
            "volatility_percentile": self.volatility_percentile,
            "volatility_signal": self.volatility_signal,
            "ranking_score": self.ranking_score,
            "target_weight_multiplier": self.target_weight_multiplier,
            "risk_cap": self.risk_cap,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class MarketRegimeSignal:
    as_of: date
    regime: str
    risk_asset_multiplier: float
    defensive_asset_multiplier: float
    conditions: Mapping[str, bool]
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "regime": self.regime,
            "risk_asset_multiplier": self.risk_asset_multiplier,
            "defensive_asset_multiplier": self.defensive_asset_multiplier,
            "conditions": dict(self.conditions),
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class SignalGenerationResult:
    as_of: date
    momentum: tuple[MomentumSignal, ...]
    trend: tuple[TrendSignal, ...]
    volatility: tuple[VolatilitySignal, ...]
    market_regime: MarketRegimeSignal

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "momentum": [signal.to_dict() for signal in self.momentum],
            "trend": [signal.to_dict() for signal in self.trend],
            "volatility": [signal.to_dict() for signal in self.volatility],
            "market_regime": self.market_regime.to_dict(),
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
