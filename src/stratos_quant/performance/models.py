from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ReturnKPIs:
    total_return: float
    cagr: float
    monthly_return: Mapping[str, float]
    annual_return: Mapping[str, float]
    best_month: float | None
    worst_month: float | None
    best_quarter: float | None
    worst_quarter: float | None
    before_fees_total_return: float | None = None
    before_fees_cagr: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_return": self.total_return,
            "cagr": self.cagr,
            "monthly_return": dict(self.monthly_return),
            "annual_return": dict(self.annual_return),
            "best_month": self.best_month,
            "worst_month": self.worst_month,
            "best_quarter": self.best_quarter,
            "worst_quarter": self.worst_quarter,
            "before_fees_total_return": self.before_fees_total_return,
            "before_fees_cagr": self.before_fees_cagr,
        }


@dataclass(frozen=True, slots=True)
class RiskKPIs:
    volatility: float
    downside_volatility: float
    max_drawdown: float
    max_drawdown_duration: int
    value_at_risk: float | None
    conditional_value_at_risk: float | None
    worst_day: float | None
    worst_month: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "volatility": self.volatility,
            "downside_volatility": self.downside_volatility,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_duration": self.max_drawdown_duration,
            "value_at_risk": self.value_at_risk,
            "conditional_value_at_risk": self.conditional_value_at_risk,
            "worst_day": self.worst_day,
            "worst_month": self.worst_month,
        }


@dataclass(frozen=True, slots=True)
class RiskAdjustedKPIs:
    sharpe_ratio: float | None
    sortino_ratio: float | None
    calmar_ratio: float | None
    information_ratio: float | None
    tracking_error: float | None
    profit_factor: float | None
    win_rate: float | None
    average_win: float | None
    average_loss: float | None
    payoff_ratio: float | None
    expectancy: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "calmar_ratio": self.calmar_ratio,
            "information_ratio": self.information_ratio,
            "tracking_error": self.tracking_error,
            "profit_factor": self.profit_factor,
            "win_rate": self.win_rate,
            "average_win": self.average_win,
            "average_loss": self.average_loss,
            "payoff_ratio": self.payoff_ratio,
            "expectancy": self.expectancy,
        }


@dataclass(frozen=True, slots=True)
class TradingCostKPIs:
    number_of_trades: int
    turnover: float
    annualized_turnover: float
    total_fees: Decimal
    total_slippage: Decimal
    average_trade_size: Decimal
    fee_drag: float
    rebalance_frequency: str
    cash_drag: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "number_of_trades": self.number_of_trades,
            "turnover": self.turnover,
            "annualized_turnover": self.annualized_turnover,
            "total_fees": self.total_fees,
            "total_slippage": self.total_slippage,
            "average_trade_size": self.average_trade_size,
            "fee_drag": self.fee_drag,
            "rebalance_frequency": self.rebalance_frequency,
            "cash_drag": self.cash_drag,
        }


@dataclass(frozen=True, slots=True)
class AllocationKPIs:
    average_asset_weight: Mapping[str, float]
    maximum_asset_weight: Mapping[str, float]
    minimum_asset_weight: Mapping[str, float]
    asset_class_exposure: Mapping[str, float]
    sector_exposure: Mapping[str, float]
    currency_exposure: Mapping[str, float]
    cash_exposure: float
    risk_contribution: Mapping[str, float]
    correlation_to_portfolio: Mapping[str, float]
    correlation_to_benchmark: Mapping[str, float]
    constraint_breaches: tuple[str, ...]
    concentration_risk_flag: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "average_asset_weight": dict(self.average_asset_weight),
            "maximum_asset_weight": dict(self.maximum_asset_weight),
            "minimum_asset_weight": dict(self.minimum_asset_weight),
            "asset_class_exposure": dict(self.asset_class_exposure),
            "sector_exposure": dict(self.sector_exposure),
            "currency_exposure": dict(self.currency_exposure),
            "cash_exposure": self.cash_exposure,
            "risk_contribution": dict(self.risk_contribution),
            "correlation_to_portfolio": dict(self.correlation_to_portfolio),
            "correlation_to_benchmark": dict(self.correlation_to_benchmark),
            "constraint_breaches": list(self.constraint_breaches),
            "concentration_risk_flag": self.concentration_risk_flag,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkKPIs:
    benchmark_return: float | None
    benchmark_cagr: float | None
    benchmark_max_drawdown: float | None
    excess_return: float | None
    rejected_after_costs: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_return": self.benchmark_return,
            "benchmark_cagr": self.benchmark_cagr,
            "benchmark_max_drawdown": self.benchmark_max_drawdown,
            "excess_return": self.excess_return,
            "rejected_after_costs": self.rejected_after_costs,
        }


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    scope: str
    as_of: date
    risk_free_rate: float
    benchmark: str | None
    returns: ReturnKPIs
    risk: RiskKPIs
    risk_adjusted: RiskAdjustedKPIs
    trading_costs: TradingCostKPIs
    allocation: AllocationKPIs
    benchmark_kpis: BenchmarkKPIs
    flags: tuple[str, ...]
    explanations: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "as_of": self.as_of.isoformat(),
            "risk_free_rate": self.risk_free_rate,
            "benchmark": self.benchmark,
            "returns": self.returns.to_dict(),
            "risk": self.risk.to_dict(),
            "risk_adjusted": self.risk_adjusted.to_dict(),
            "trading_costs": self.trading_costs.to_dict(),
            "allocation": self.allocation.to_dict(),
            "benchmark_kpis": self.benchmark_kpis.to_dict(),
            "flags": list(self.flags),
            "explanations": dict(self.explanations),
        }
