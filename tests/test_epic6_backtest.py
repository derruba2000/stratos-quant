from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from stratos_quant.backtest import BacktestConfig, BacktestEngine
from stratos_quant.strategy import AllocationResult, AssetClassSignal


class RecordingAllocationStrategy:
    def __init__(self, weights):
        self.weights = weights
        self.calls = []

    def allocate(self, prices, *, as_of=None):
        self.calls.append(
            {
                "as_of": as_of,
                "max_date_seen": pd.Timestamp(prices["date"].max()).date(),
            }
        )
        return AllocationResult(
            model="TEST",
            as_of=as_of,
            weights=self.weights,
            signals=(
                AssetClassSignal(
                    asset_class_code="EQUITY",
                    trend_positive=True,
                    momentum_12m=0.2,
                    annualized_volatility=0.1,
                    security_count=2,
                ),
            ),
        )


def _prices() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=70)
    rows = []
    for index, timestamp in enumerate(dates):
        rows.extend(
            [
                {
                    "security_id": 1,
                    "ticker": "AAA",
                    "asset_class_code": "EQUITY",
                    "date": timestamp,
                    "close": 100 + index * 0.50,
                },
                {
                    "security_id": 2,
                    "ticker": "BBB",
                    "asset_class_code": "EQUITY",
                    "date": timestamp,
                    "close": 80 + index * 0.25 + np.sin(index / 5),
                },
                {
                    "security_id": 3,
                    "ticker": "SPY",
                    "asset_class_code": "BENCHMARK",
                    "date": timestamp,
                    "close": 100 + index * 0.30,
                },
            ]
        )
    return pd.DataFrame(rows)


def test_single_strategy_backtest_is_walk_forward_and_records_artifacts():
    strategy = RecordingAllocationStrategy({"EQUITY": Decimal("1.0000000000")})
    config = BacktestConfig(
        strategy_id="equity-test",
        symbols=("AAA", "BBB"),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 4, 9),
        initial_cash=Decimal("10000"),
        rebalance_frequency="MONTHLY",
        benchmark="SPY",
        fixed_trade_fee=Decimal("1"),
        broker_fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.001"),
        drift_threshold=Decimal("0.01"),
    )

    result = BacktestEngine().run(_prices(), strategy=strategy, config=config)

    assert result.orders
    assert result.trades
    assert result.positions
    assert result.equity_curve
    assert result.rebalance_allocations
    assert all(
        call["max_date_seen"] == call["as_of"]
        for call in strategy.calls
    )
    assert result.metrics.total_fees > Decimal("0")
    assert result.metrics.total_slippage > Decimal("0")
    assert result.metrics.benchmark_return is not None
    assert result.metrics.excess_return is not None
    assert result.metrics.tracking_error is not None
    assert result.metrics.max_drawdown <= 0


def test_multi_asset_backtest_rebalances_to_valid_allocations_after_costs():
    strategy = RecordingAllocationStrategy(
        {
            "EQUITY": Decimal("0.7000000000"),
            "BOND": Decimal("0.3000000000"),
        }
    )
    prices = _prices()
    bond_rows = []
    for index, timestamp in enumerate(pd.bdate_range("2026-01-01", periods=70)):
        bond_rows.append(
            {
                "security_id": 4,
                "ticker": "BND",
                "asset_class_code": "BOND",
                "date": timestamp,
                "close": 50 + index * 0.05,
            }
        )
    prices = pd.concat([prices, pd.DataFrame(bond_rows)], ignore_index=True)
    config = BacktestConfig(
        strategy_id="portfolio-test",
        symbols=("AAA", "BBB", "BND"),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 4, 9),
        initial_cash=Decimal("10000"),
        rebalance_frequency="WEEKLY",
        fixed_trade_fee=Decimal("0.50"),
        slippage_rate=Decimal("0.0005"),
    )

    result = BacktestEngine().run(prices, strategy=strategy, config=config)

    assert result.walk_forward_windows
    assert all(
        sum(weights.values(), Decimal("0")) == Decimal("1.0000000000")
        for weights in result.rebalance_allocations.values()
    )
    assert result.equity_curve[-1].equity > Decimal("0")
    assert result.metrics.turnover > 0
    assert result.metrics.total_fees > Decimal("0")


def test_walk_forward_flags_possible_overfitting_when_sample_is_tiny():
    strategy = RecordingAllocationStrategy({"EQUITY": Decimal("1.0000000000")})
    config = BacktestConfig(
        strategy_id="tiny",
        symbols=("AAA", "BBB"),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 5),
        initial_cash=Decimal("10000"),
        rebalance_frequency="MONTHLY",
    )

    result = BacktestEngine().run(_prices(), strategy=strategy, config=config)

    assert "Too few walk-forward windows" in result.overfitting_warnings[0]
