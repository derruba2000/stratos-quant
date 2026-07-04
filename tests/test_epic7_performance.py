from __future__ import annotations

from decimal import Decimal

import pandas as pd
from sqlalchemy import create_engine

from stratos_quant.performance import PerformanceKPIEngine, PerformanceKPIRepository


def _equity_curve() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=90)
    values = []
    benchmark = []
    for index, timestamp in enumerate(dates):
        trend = 10000 + index * 25
        wobble = -350 if 35 <= index <= 42 else 0
        values.append(trend + wobble)
        benchmark.append(10000 + index * 18)
    return pd.DataFrame(
        {
            "timestamp": dates,
            "equity": values,
            "fees_paid": [0] * 30 + [12] * 30 + [24] * 30,
            "slippage_paid": [0] * 30 + [6] * 30 + [12] * 30,
            "benchmark_equity": benchmark,
        }
    )


def _trades() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": "2026-01-02",
                "ticker": "AAA",
                "side": "BUY",
                "trade_value": Decimal("6000"),
                "estimated_fees": Decimal("6"),
                "estimated_slippage": Decimal("3"),
            },
            {
                "timestamp": "2026-02-02",
                "ticker": "BBB",
                "side": "BUY",
                "trade_value": Decimal("2500"),
                "estimated_fees": Decimal("4"),
                "estimated_slippage": Decimal("2"),
            },
            {
                "timestamp": "2026-03-02",
                "ticker": "AAA",
                "side": "SELL",
                "trade_value": Decimal("1200"),
                "estimated_fees": Decimal("2"),
                "estimated_slippage": Decimal("1"),
            },
        ]
    )


def _positions() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=6)
    rows = []
    for timestamp in dates:
        rows.extend(
            [
                {
                    "timestamp": timestamp,
                    "ticker": "AAA",
                    "asset_class_code": "EQUITY",
                    "sector": "TECH",
                    "currency": "USD",
                    "weight": 0.62,
                },
                {
                    "timestamp": timestamp,
                    "ticker": "BBB",
                    "asset_class_code": "BOND",
                    "sector": "GOVT",
                    "currency": "USD",
                    "weight": 0.28,
                },
            ]
        )
    return pd.DataFrame(rows)


def _asset_returns() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=90)
    return pd.DataFrame(
        {
            "AAA": [0.001 + (0.004 if index % 7 == 0 else 0) for index in range(90)],
            "BBB": [0.0003 for _ in range(90)],
        },
        index=dates,
    )


def test_epic7_calculates_return_risk_adjusted_and_cost_kpis():
    report = PerformanceKPIEngine().calculate(
        _equity_curve(),
        trades=_trades(),
        positions=_positions(),
        risk_free_rate=0.02,
        benchmark="SPY",
        rebalance_frequency="MONTHLY",
        asset_returns=_asset_returns(),
        max_asset_weight=0.60,
    )

    assert report.returns.total_return > 0
    assert report.returns.before_fees_total_return > report.returns.total_return
    assert report.returns.monthly_return
    assert report.risk.volatility > 0
    assert report.risk.downside_volatility >= 0
    assert report.risk.max_drawdown < 0
    assert report.risk.max_drawdown_duration > 0
    assert report.risk.value_at_risk is not None
    assert report.risk_adjusted.sharpe_ratio is not None
    assert report.risk_adjusted.profit_factor is not None
    assert report.risk_adjusted.win_rate is not None
    assert report.benchmark_kpis.benchmark_return is not None
    assert report.benchmark_kpis.excess_return is not None
    assert report.trading_costs.number_of_trades == 3
    assert report.trading_costs.total_fees == Decimal("12.00")
    assert report.trading_costs.total_slippage == Decimal("6.00")
    assert report.trading_costs.average_trade_size == Decimal("3233.33")
    assert report.trading_costs.fee_drag > 0
    assert "AAA exceeded max asset weight" in report.flags[0]
    assert "Sharpe" in report.explanations["risk_adjusted"]


def test_epic7_calculates_allocation_exposure_and_correlations():
    report = PerformanceKPIEngine().calculate(
        _equity_curve(),
        trades=_trades(),
        positions=_positions(),
        benchmark="SPY",
        asset_returns=_asset_returns(),
    )

    assert report.allocation.average_asset_weight["AAA"] == 0.62
    assert report.allocation.maximum_asset_weight["BBB"] == 0.28
    assert report.allocation.asset_class_exposure == {
        "BOND": 0.28,
        "EQUITY": 0.62,
    }
    assert report.allocation.sector_exposure == {"GOVT": 0.28, "TECH": 0.62}
    assert report.allocation.currency_exposure == {"USD": 0.9}
    assert report.allocation.cash_exposure == 0.09999999999999998
    assert set(report.allocation.risk_contribution) == {"AAA", "BBB"}
    assert set(report.allocation.correlation_to_portfolio) == {"AAA", "BBB"}
    assert set(report.allocation.correlation_to_benchmark) == {"AAA", "BBB"}


def test_epic7_persists_kpi_snapshots_historically(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'kpis.sqlite3'}")
    repository = PerformanceKPIRepository(engine)
    report = PerformanceKPIEngine().calculate(
        _equity_curve(),
        trades=_trades(),
        positions=_positions(),
        benchmark="SPY",
        rebalance_frequency="MONTHLY",
    )

    first_id = repository.save(report)
    second_id = repository.save(report)
    latest = repository.latest(scope="PORTFOLIO")

    assert second_id == first_id + 1
    assert latest["id"] == second_id
    assert latest["scope"] == "PORTFOLIO"
    assert latest["payload_json"]
