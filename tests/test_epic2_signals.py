from __future__ import annotations

import numpy as np
import pandas as pd

from stratos_quant.strategy import (
    generate_market_regime_signal,
    generate_momentum_signals,
    generate_signal_suite,
    generate_trend_signals,
    generate_volatility_signals,
)


def _signal_prices() -> pd.DataFrame:
    dates = pd.bdate_range("2022-01-03", periods=820)
    index = np.arange(len(dates), dtype=float)
    series = {
        (1, "WINNER", "EQUITY"): 100 + index * 0.18,
        (2, "LOSER", "EQUITY"): 180 - index * 0.12,
        (3, "CHOP", "BOND"): 100 + np.sin(index / 8.0),
        (4, "BTC", "CRYPTO"): np.concatenate(
            [
                np.linspace(100, 260, 520),
                np.linspace(260, 90, 300),
            ]
        ),
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
                    "close": max(float(close), 1.0),
                }
            )
    return pd.DataFrame(rows)


def _risk_off_prices() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=320)
    index = np.arange(len(dates), dtype=float)
    equity = np.concatenate(
        [
            np.linspace(140, 180, 220),
            180 - np.arange(100, dtype=float) * 1.2 + np.sin(np.arange(100)) * 4,
        ]
    )
    crypto = np.concatenate(
        [
            np.linspace(80, 240, 180),
            np.linspace(240, 70, 140),
        ]
    )
    bond = 100 - index * 0.03
    rows = []
    for security_id, ticker, asset_class, closes in (
        (1, "SPY", "EQUITY", equity),
        (2, "BTC", "CRYPTO", crypto),
        (3, "AGG", "BOND", bond),
    ):
        for timestamp, close in zip(dates, closes, strict=True):
            rows.append(
                {
                    "security_id": security_id,
                    "ticker": ticker,
                    "asset_class_code": asset_class,
                    "date": timestamp,
                    "close": max(float(close), 1.0),
                }
            )
    return pd.DataFrame(rows)


def test_momentum_signals_include_24m_and_36m_lookbacks_with_explanations():
    signals = {
        signal.ticker: signal
        for signal in generate_momentum_signals(_signal_prices())
    }

    winner = signals["WINNER"]
    loser = signals["LOSER"]

    assert set(winner.returns) == {"1m", "3m", "6m", "12m", "24m", "36m"}
    assert winner.returns["24m"] > 0
    assert winner.returns["36m"] > winner.returns["24m"]
    assert winner.momentum_signal == "bullish"
    assert winner.confidence_score > 0
    assert "24-month" in winner.explanation
    assert loser.momentum_signal == "bearish"
    assert "negative lookbacks" in loser.explanation


def test_trend_signals_are_backtest_safe_and_classify_regimes():
    signals = {
        signal.ticker: signal
        for signal in generate_trend_signals(_signal_prices())
    }

    assert signals["WINNER"].trend_signal == "bullish"
    assert signals["WINNER"].trend_positive
    assert signals["LOSER"].trend_signal == "bearish"
    assert signals["LOSER"].drawdown_worsening
    assert signals["CHOP"].trend_signal == "neutral"
    assert signals["WINNER"].as_of == _signal_prices()["date"].max().date()


def test_volatility_signals_produce_ranking_and_risk_sizing_hints():
    signals = {
        signal.ticker: signal
        for signal in generate_volatility_signals(_risk_off_prices())
    }

    assert signals["BTC"].volatility_signal == "high"
    assert signals["BTC"].target_weight_multiplier == 0.50
    assert signals["BTC"].risk_cap == 0.05
    assert signals["AGG"].ranking_score > signals["BTC"].ranking_score
    assert signals["AGG"].target_weight_multiplier >= 1.0


def test_bearish_risk_signal_classifies_risk_off_and_explains_conditions():
    prices = _risk_off_prices()
    regime = generate_market_regime_signal(
        prices,
        equity_benchmark_ticker="SPY",
    )

    assert regime.regime == "risk-off"
    assert regime.risk_asset_multiplier < 1
    assert regime.defensive_asset_multiplier > 1
    assert regime.conditions["equity_benchmark_below_200d_ma"]
    assert regime.conditions["large_market_drawdown"]
    assert regime.conditions["crypto_drawdown_above_threshold"]
    assert "risk-off" in regime.explanation


def test_signal_suite_returns_all_epic2_outputs():
    result = generate_signal_suite(
        _risk_off_prices(),
        equity_benchmark_ticker="SPY",
    )

    assert result.momentum
    assert result.trend
    assert result.volatility
    assert result.market_regime.regime == "risk-off"
    serialized = result.to_dict()
    assert serialized["market_regime"]["regime"] == "risk-off"
