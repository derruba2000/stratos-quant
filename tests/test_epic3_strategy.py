from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine

from stratos_quant.strategy import (
    EnsembleAllocationEngine,
    HierarchicalAllocationEngine,
    InsufficientPriceHistoryError,
    PriceHistoryLoader,
)
from stratos_quant.strategy.models import ONE


def _strategy_prices(*, bearish: bool = False) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=300)
    index = np.arange(len(dates), dtype=float)
    if bearish:
        series = {
            (1, "EQUITY", "EQUITY"): 150 - index * 0.20,
            (2, "BOND", "BOND"): 120 - index * 0.05,
        }
    else:
        series = {
            (1, "EQUITY", "EQUITY"): 100 + index * 0.30 + np.sin(index / 3) * 3,
            (2, "BOND", "BOND"): 100 + index * 0.05 + np.sin(index / 15) * 0.2,
            (3, "LOSER", "COMMODITY"): 130 - index * 0.10,
        }

    rows = []
    for (security_id, ticker, asset_class), closes in series.items():
        for timestamp, close in zip(dates, closes, strict=True):
            rows.append(
                {
                    "security_id": security_id,
                    "ticker": ticker,
                    "asset_class_code": asset_class,
                    "date": timestamp,
                    "close": close,
                }
            )
    return pd.DataFrame(rows)


def test_hierarchical_selects_momentum_winner_and_scales_volatility():
    result = HierarchicalAllocationEngine(target_volatility=0.01).allocate(
        _strategy_prices()
    )

    assert result.model == "HIERARCHICAL"
    assert sum(result.weights.values()) == ONE
    assert set(result.weights) == {"CASH", "EQUITY"}
    assert Decimal("0") < result.weights["EQUITY"] < ONE
    assert result.weights["CASH"] > Decimal("0")

    signals = {signal.asset_class_code: signal for signal in result.signals}
    assert signals["EQUITY"].trend_positive
    assert signals["EQUITY"].momentum_12m > signals["BOND"].momentum_12m
    assert not signals["COMMODITY"].trend_positive


def test_hierarchical_falls_back_to_cash_when_no_absolute_momentum():
    result = HierarchicalAllocationEngine().allocate(_strategy_prices(bearish=True))

    assert result.weights == {"CASH": ONE}


def test_ensemble_blends_three_independent_normalized_votes():
    result = EnsembleAllocationEngine().allocate(_strategy_prices())

    assert result.model == "ENSEMBLE"
    assert sum(result.weights.values()) == ONE
    assert set(result.component_weights) == {
        "moving_average",
        "dual_momentum",
        "volatility_scaler",
    }
    assert all(
        sum(weights.values()) == ONE
        for weights in result.component_weights.values()
    )
    assert result.component_weights["dual_momentum"] == {"EQUITY": ONE}
    assert set(result.component_weights["moving_average"]) == {"BOND", "EQUITY"}
    assert result.weights["EQUITY"] > result.weights.get("COMMODITY", Decimal("0"))

    serialized = result.to_dict()
    assert sum(Decimal(value) for value in serialized["weights"].values()) == ONE
    assert all(len(value.split(".")[1]) == 10 for value in serialized["weights"].values())


def test_engines_reject_unusable_price_history():
    short_prices = _strategy_prices().groupby("ticker").head(20)

    with pytest.raises(InsufficientPriceHistoryError, match="required"):
        HierarchicalAllocationEngine().allocate(short_prices)


def test_price_loader_filters_dates_and_maps_strategy_asset_classes(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'prices.sqlite3'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE securities (
                id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                asset_class TEXT NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE price_history (
                security_id INTEGER NOT NULL,
                date DATE NOT NULL,
                close NUMERIC NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO securities VALUES (1, 'SPY', 'ETF'), (2, 'AGG', 'ETF')"
        )
        connection.exec_driver_sql(
            """
            INSERT INTO price_history VALUES
                (1, '2025-01-01', 100),
                (1, '2025-01-02', 101),
                (2, '2025-01-01', 90),
                (2, '2025-01-02', 91)
            """
        )

    frame = PriceHistoryLoader(engine).load(
        security_ids=[1, 2],
        as_of=date(2025, 1, 1),
        asset_class_map={"SPY": "EQUITY", "AGG": "BOND"},
    )

    assert len(frame) == 2
    assert set(frame["asset_class_code"]) == {"EQUITY", "BOND"}
    assert frame["date"].max().date() == date(2025, 1, 1)
