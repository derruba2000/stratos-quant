from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    strategy_id: str
    symbols: tuple[str, ...]
    start_date: date
    end_date: date
    initial_cash: Decimal
    rebalance_frequency: str = "MONTHLY"
    benchmark: str | None = None
    fixed_trade_fee: Decimal = Decimal("0")
    broker_fee_rate: Decimal = Decimal("0")
    slippage_rate: Decimal = Decimal("0")
    drift_threshold: Decimal = Decimal("0")
    fractional_shares: bool = True


@dataclass(frozen=True, slots=True)
class BacktestOrder:
    timestamp: date
    ticker: str
    side: str
    target_weight: Decimal
    current_weight: Decimal
    trade_value: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "ticker": self.ticker,
            "side": self.side,
            "target_weight": self.target_weight,
            "current_weight": self.current_weight,
            "trade_value": self.trade_value,
        }


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    timestamp: date
    ticker: str
    side: str
    quantity: Decimal
    execution_price: Decimal
    trade_value: Decimal
    estimated_fees: Decimal
    estimated_slippage: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "ticker": self.ticker,
            "side": self.side,
            "quantity": self.quantity,
            "execution_price": self.execution_price,
            "trade_value": self.trade_value,
            "estimated_fees": self.estimated_fees,
            "estimated_slippage": self.estimated_slippage,
        }


@dataclass(frozen=True, slots=True)
class BacktestPosition:
    timestamp: date
    ticker: str
    quantity: Decimal
    close: Decimal
    market_value: Decimal
    weight: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "ticker": self.ticker,
            "quantity": self.quantity,
            "close": self.close,
            "market_value": self.market_value,
            "weight": self.weight,
        }


@dataclass(frozen=True, slots=True)
class EquityCurvePoint:
    timestamp: date
    equity: Decimal
    cash: Decimal
    fees_paid: Decimal
    slippage_paid: Decimal
    benchmark_equity: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "equity": self.equity,
            "cash": self.cash,
            "fees_paid": self.fees_paid,
            "slippage_paid": self.slippage_paid,
            "benchmark_equity": self.benchmark_equity,
        }


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    total_return: float
    cagr: float
    volatility: float
    sharpe_ratio: float | None
    sortino_ratio: float | None
    calmar_ratio: float | None
    max_drawdown: float
    turnover: float
    total_fees: Decimal
    total_slippage: Decimal
    benchmark_return: float | None
    excess_return: float | None
    information_ratio: float | None
    tracking_error: float | None
    benchmark_max_drawdown: float | None
    rejected_after_costs: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_return": self.total_return,
            "cagr": self.cagr,
            "volatility": self.volatility,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "calmar_ratio": self.calmar_ratio,
            "max_drawdown": self.max_drawdown,
            "turnover": self.turnover,
            "total_fees": self.total_fees,
            "total_slippage": self.total_slippage,
            "benchmark_return": self.benchmark_return,
            "excess_return": self.excess_return,
            "information_ratio": self.information_ratio,
            "tracking_error": self.tracking_error,
            "benchmark_max_drawdown": self.benchmark_max_drawdown,
            "rejected_after_costs": self.rejected_after_costs,
        }


@dataclass(frozen=True, slots=True)
class BacktestResult:
    config: BacktestConfig
    orders: tuple[BacktestOrder, ...]
    trades: tuple[BacktestTrade, ...]
    positions: tuple[BacktestPosition, ...]
    equity_curve: tuple[EquityCurvePoint, ...]
    rebalance_allocations: Mapping[date, Mapping[str, Decimal]]
    metrics: BacktestMetrics
    walk_forward_windows: tuple[date, ...]
    overfitting_warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": {
                "strategy_id": self.config.strategy_id,
                "symbols": list(self.config.symbols),
                "start_date": self.config.start_date.isoformat(),
                "end_date": self.config.end_date.isoformat(),
                "initial_cash": self.config.initial_cash,
                "rebalance_frequency": self.config.rebalance_frequency,
                "benchmark": self.config.benchmark,
            },
            "orders": [order.to_dict() for order in self.orders],
            "trades": [trade.to_dict() for trade in self.trades],
            "positions": [position.to_dict() for position in self.positions],
            "equity_curve": [point.to_dict() for point in self.equity_curve],
            "rebalance_allocations": {
                key.isoformat(): dict(value)
                for key, value in self.rebalance_allocations.items()
            },
            "metrics": self.metrics.to_dict(),
            "walk_forward_windows": [
                item.isoformat() for item in self.walk_forward_windows
            ],
            "overfitting_warnings": list(self.overfitting_warnings),
        }
