from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd
from pybroker import returnv

from .models import (
    AssetClassSignal,
    MarketRegimeSignal,
    MomentumSignal,
    SecuritySignal,
    SignalGenerationResult,
    TrendSignal,
    VolatilitySignal,
)


TRADING_DAYS = 252
MOMENTUM_WINDOWS = {
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "12m": 252,
    "24m": 504,
    "36m": 756,
}
MOMENTUM_LABELS = {
    "1m": "1-month",
    "3m": "3-month",
    "6m": "6-month",
    "12m": "12-month",
    "24m": "24-month",
    "36m": "36-month",
}
DEFAULT_CRYPTO_ASSET_CLASSES = frozenset({"CRYPTO", "DIGITAL_ASSET"})


def generate_signal_suite(
    prices: pd.DataFrame,
    *,
    benchmark_prices: pd.DataFrame | None = None,
    as_of: pd.Timestamp | None = None,
    equity_benchmark_ticker: str | None = None,
) -> SignalGenerationResult:
    """Generate the complete Epic 2 signal package from historical prices."""
    if prices.empty:
        raise ValueError("No price history was supplied")
    clean_prices = _prepare_price_frame(prices, as_of=as_of)
    resolved_as_of = pd.Timestamp(clean_prices["date"].max()).date()
    momentum = generate_momentum_signals(
        clean_prices,
        benchmark_prices=benchmark_prices,
    )
    trend = generate_trend_signals(clean_prices)
    volatility = generate_volatility_signals(clean_prices)
    market_regime = generate_market_regime_signal(
        clean_prices,
        momentum_signals=momentum,
        volatility_signals=volatility,
        equity_benchmark_ticker=equity_benchmark_ticker,
    )
    return SignalGenerationResult(
        as_of=resolved_as_of,
        momentum=momentum,
        trend=trend,
        volatility=volatility,
        market_regime=market_regime,
    )


def generate_momentum_signals(
    prices: pd.DataFrame,
    *,
    benchmark_prices: pd.DataFrame | None = None,
    bullish_threshold: float = 0.02,
    bearish_threshold: float = -0.02,
) -> tuple[MomentumSignal, ...]:
    """Generate bullish, bearish, or neutral momentum signals per security."""
    benchmark_returns = _benchmark_returns(benchmark_prices)
    signals: list[MomentumSignal] = []
    for security_id, ticker, asset_class, group in _iter_security_groups(prices):
        closes = _closes(group)
        if len(closes) < 2:
            continue
        as_of = pd.Timestamp(group["date"].max()).date()
        returns = {
            label: _period_return(closes, window)
            for label, window in MOMENTUM_WINDOWS.items()
        }
        available = [value for value in returns.values() if value is not None]
        if not available:
            score = 0.0
        else:
            score = float(np.mean(available))
        signal = _three_state_signal(
            score,
            bullish_threshold=bullish_threshold,
            bearish_threshold=bearish_threshold,
        )
        confidence = float(min(1.0, abs(score) / 0.30))
        benchmark_return = benchmark_returns.get(str(ticker).upper())
        relative_strength = (
            score - benchmark_return
            if benchmark_return is not None
            else None
        )
        explanation = _momentum_explanation(
            returns,
            signal,
            relative_strength,
        )
        signals.append(
            MomentumSignal(
                security_id=int(security_id),
                ticker=str(ticker),
                asset_class_code=str(asset_class),
                as_of=as_of,
                returns=returns,
                momentum_score=score,
                momentum_signal=signal,
                confidence_score=confidence,
                relative_strength_vs_benchmark=relative_strength,
                explanation=explanation,
            )
        )
    return tuple(signals)


def generate_trend_signals(
    prices: pd.DataFrame,
    *,
    short_window: int = 50,
    long_window: int = 200,
    recent_window: int = 21,
) -> tuple[TrendSignal, ...]:
    """Classify each security as bullish, bearish, or neutral by trend regime."""
    signals: list[TrendSignal] = []
    for security_id, ticker, asset_class, group in _iter_security_groups(prices):
        closes = _closes(group)
        if closes.empty:
            continue
        as_of = pd.Timestamp(group["date"].max()).date()
        close = float(closes.iloc[-1])
        short_ma = _moving_average(closes, short_window)
        long_ma = _moving_average(closes, long_window)
        recent_return = _period_return(closes, recent_window)
        drawdowns = closes / closes.cummax() - 1.0
        drawdown = float(drawdowns.iloc[-1])
        previous_drawdown = (
            float(drawdowns.iloc[-recent_window])
            if len(drawdowns) >= recent_window
            else None
        )
        drawdown_worsening = (
            previous_drawdown is not None
            and drawdown < previous_drawdown
        )
        bullish = (
            short_ma is not None
            and long_ma is not None
            and recent_return is not None
            and close > short_ma
            and short_ma > long_ma
            and recent_return > 0
        )
        bearish = (
            long_ma is not None
            and short_ma is not None
            and close < long_ma
            and short_ma < long_ma
            and (drawdown_worsening or (recent_return is not None and recent_return < 0))
        )
        if bullish:
            signal = "bullish"
        elif bearish:
            signal = "bearish"
        else:
            signal = "neutral"
        explanation = _trend_explanation(
            signal,
            close,
            short_ma,
            long_ma,
            recent_return,
            drawdown_worsening,
        )
        signals.append(
            TrendSignal(
                security_id=int(security_id),
                ticker=str(ticker),
                asset_class_code=str(asset_class),
                as_of=as_of,
                close=close,
                moving_average_50d=short_ma,
                moving_average_200d=long_ma,
                recent_return_1m=recent_return,
                drawdown=drawdown,
                drawdown_worsening=drawdown_worsening,
                trend_signal=signal,
                explanation=explanation,
            )
        )
    return tuple(signals)


def generate_volatility_signals(
    prices: pd.DataFrame,
    *,
    crypto_asset_classes: Iterable[str] = DEFAULT_CRYPTO_ASSET_CLASSES,
) -> tuple[VolatilitySignal, ...]:
    """Estimate volatility and risk sizing hints using only trailing data."""
    crypto_codes = {code.upper() for code in crypto_asset_classes}
    signals: list[VolatilitySignal] = []
    for security_id, ticker, asset_class, group in _iter_security_groups(prices):
        closes = _closes(group)
        if len(closes) < 2:
            continue
        returns = closes.pct_change().dropna()
        as_of = pd.Timestamp(group["date"].max()).date()
        vol20 = _annualized_volatility(returns.tail(20))
        vol60 = _annualized_volatility(returns.tail(60))
        vol252 = _annualized_volatility(returns.tail(252))
        downside = _annualized_volatility(returns[returns < 0].tail(252))
        rolling60 = returns.rolling(60).std(ddof=1).dropna() * np.sqrt(TRADING_DAYS)
        percentile = None
        if vol60 is not None and not rolling60.empty:
            percentile = float((rolling60 <= vol60).mean())
        if (
            vol60 is not None
            and vol252 is not None
            and vol60 < 0.05
            and vol252 < 0.05
        ):
            volatility_signal = "low"
        elif percentile is None:
            volatility_signal = "normal"
        elif percentile >= 0.80:
            volatility_signal = "high"
        elif percentile <= 0.30:
            volatility_signal = "low"
        else:
            volatility_signal = "normal"
        volatility_for_rank = vol60 if vol60 is not None else vol252
        ranking_score = (
            1.0 / (1.0 + volatility_for_rank)
            if volatility_for_rank is not None
            else 0.5
        )
        if volatility_signal == "high":
            multiplier = 0.50
        elif volatility_signal == "low":
            multiplier = 1.10
        else:
            multiplier = 1.0
        risk_cap = (
            0.05
            if str(asset_class).upper() in crypto_codes
            and volatility_signal == "high"
            else None
        )
        explanation = _volatility_explanation(
            volatility_signal,
            vol20,
            vol60,
            vol252,
            percentile,
            risk_cap,
        )
        signals.append(
            VolatilitySignal(
                security_id=int(security_id),
                ticker=str(ticker),
                asset_class_code=str(asset_class),
                as_of=as_of,
                rolling_20d_volatility=vol20,
                rolling_60d_volatility=vol60,
                rolling_252d_volatility=vol252,
                downside_volatility=downside,
                volatility_percentile=percentile,
                volatility_signal=volatility_signal,
                ranking_score=float(ranking_score),
                target_weight_multiplier=multiplier,
                risk_cap=risk_cap,
                explanation=explanation,
            )
        )
    return tuple(signals)


def generate_market_regime_signal(
    prices: pd.DataFrame,
    *,
    momentum_signals: Iterable[MomentumSignal] | None = None,
    volatility_signals: Iterable[VolatilitySignal] | None = None,
    equity_benchmark_ticker: str | None = None,
) -> MarketRegimeSignal:
    """Classify the market as risk-on, neutral, or risk-off."""
    clean_prices = _prepare_price_frame(prices)
    resolved_as_of = pd.Timestamp(clean_prices["date"].max()).date()
    benchmark_group = _select_equity_benchmark(clean_prices, equity_benchmark_ticker)
    benchmark_closes = (
        _closes(benchmark_group)
        if benchmark_group is not None
        else pd.Series(dtype=float)
    )
    benchmark_long_ma = _moving_average(benchmark_closes, 200)
    benchmark_below_200d = (
        bool(float(benchmark_closes.iloc[-1]) < benchmark_long_ma)
        if benchmark_long_ma is not None and not benchmark_closes.empty
        else False
    )
    benchmark_returns = benchmark_closes.pct_change().dropna()
    vol20 = _annualized_volatility(benchmark_returns.tail(20))
    vol60 = _annualized_volatility(benchmark_returns.tail(60))
    rising_volatility = (
        bool(vol20 > vol60 * 1.25)
        if vol20 is not None and vol60 is not None and vol60 > 0
        else False
    )
    drawdown = (
        float(benchmark_closes.iloc[-1] / benchmark_closes.cummax().iloc[-1] - 1.0)
        if not benchmark_closes.empty
        else 0.0
    )
    large_market_drawdown = drawdown <= -0.15
    momentum = tuple(momentum_signals or generate_momentum_signals(clean_prices))
    asset_class_scores: dict[str, list[float]] = {}
    for signal in momentum:
        asset_class_scores.setdefault(signal.asset_class_code, []).append(
            signal.momentum_score
        )
    negative_classes = [
        code
        for code, scores in asset_class_scores.items()
        if scores and float(np.mean(scores)) < 0
    ]
    negative_momentum_breadth = (
        bool(asset_class_scores)
        and len(negative_classes) / len(asset_class_scores) >= 0.50
    )
    crypto_drawdown = False
    for _, _, asset_class, group in _iter_security_groups(clean_prices):
        if str(asset_class).upper() not in DEFAULT_CRYPTO_ASSET_CLASSES:
            continue
        closes = _closes(group)
        if closes.empty:
            continue
        crypto_drawdown = crypto_drawdown or (
            float(closes.iloc[-1] / closes.cummax().iloc[-1] - 1.0) <= -0.30
        )
    volatility = tuple(
        volatility_signals or generate_volatility_signals(clean_prices)
    )
    high_volatility_breadth = bool(volatility) and (
        sum(signal.volatility_signal == "high" for signal in volatility)
        / len(volatility)
        >= 0.50
    )
    conditions = {
        "equity_benchmark_below_200d_ma": benchmark_below_200d,
        "rising_volatility": rising_volatility or high_volatility_breadth,
        "large_market_drawdown": large_market_drawdown,
        "negative_momentum_across_multiple_asset_classes": negative_momentum_breadth,
        "credit_bond_stress_available": False,
        "crypto_drawdown_above_threshold": crypto_drawdown,
    }
    active_bearish_conditions = sum(conditions.values())
    if active_bearish_conditions >= 3:
        regime = "risk-off"
        risk_multiplier = 0.50
        defensive_multiplier = 1.25
    elif active_bearish_conditions <= 1:
        regime = "risk-on"
        risk_multiplier = 1.0
        defensive_multiplier = 0.90
    else:
        regime = "neutral"
        risk_multiplier = 0.80
        defensive_multiplier = 1.0
    active = [name for name, value in conditions.items() if value]
    explanation = (
        f"Regime is {regime}; active bearish conditions: "
        f"{', '.join(active) if active else 'none'}."
    )
    return MarketRegimeSignal(
        as_of=resolved_as_of,
        regime=regime,
        risk_asset_multiplier=risk_multiplier,
        defensive_asset_multiplier=defensive_multiplier,
        conditions=conditions,
        explanation=explanation,
    )


def _prepare_price_frame(
    prices: pd.DataFrame,
    *,
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    frame = prices.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["close"])
    frame = frame[frame["close"] > 0]
    if as_of is not None:
        frame = frame[frame["date"] <= pd.Timestamp(as_of)]
    return frame.sort_values(["security_id", "date"]).reset_index(drop=True)


def _iter_security_groups(prices: pd.DataFrame):
    frame = _prepare_price_frame(prices)
    for (security_id, ticker, asset_class), group in frame.groupby(
        ["security_id", "ticker", "asset_class_code"],
        sort=True,
    ):
        yield security_id, ticker, asset_class, group


def _closes(group: pd.DataFrame) -> pd.Series:
    return (
        group.sort_values("date")
        .drop_duplicates("date", keep="last")["close"]
        .astype(float)
        .reset_index(drop=True)
    )


def _period_return(closes: pd.Series, window: int) -> float | None:
    if len(closes) <= window:
        return None
    start = float(closes.iloc[-window - 1])
    end = float(closes.iloc[-1])
    if start <= 0:
        return None
    return float(end / start - 1.0)


def _moving_average(closes: pd.Series, window: int) -> float | None:
    if len(closes) < window:
        return None
    return float(closes.tail(window).mean())


def _annualized_volatility(returns: pd.Series) -> float | None:
    clean = returns.replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < 2:
        return None
    return float(clean.std(ddof=1) * np.sqrt(TRADING_DAYS))


def _three_state_signal(
    score: float,
    *,
    bullish_threshold: float,
    bearish_threshold: float,
) -> str:
    if score > bullish_threshold:
        return "bullish"
    if score < bearish_threshold:
        return "bearish"
    return "neutral"


def _benchmark_returns(
    benchmark_prices: pd.DataFrame | None,
) -> dict[str, float]:
    if benchmark_prices is None or benchmark_prices.empty:
        return {}
    frame = benchmark_prices.copy()
    if "ticker" not in frame:
        return {}
    returns: dict[str, float] = {}
    for ticker, group in frame.groupby("ticker", sort=True):
        closes = _closes(
            group.assign(
                security_id=0,
                asset_class_code="BENCHMARK",
            )
        )
        value = _period_return(closes, MOMENTUM_WINDOWS["12m"])
        if value is not None:
            returns[str(ticker).upper()] = value
    return returns


def _momentum_explanation(
    returns: Mapping[str, float | None],
    signal: str,
    relative_strength: float | None,
) -> str:
    available = {
        MOMENTUM_LABELS[label]: value
        for label, value in returns.items()
        if value is not None
    }
    if not available:
        return "Momentum is neutral because there is not enough price history."
    positive = [label for label, value in available.items() if value > 0]
    negative = [label for label, value in available.items() if value < 0]
    parts = [
        f"Momentum is {signal}",
        f"positive lookbacks: {', '.join(positive) if positive else 'none'}",
        f"negative lookbacks: {', '.join(negative) if negative else 'none'}",
    ]
    if relative_strength is not None:
        direction = "above" if relative_strength >= 0 else "below"
        parts.append(f"relative strength is {direction} benchmark")
    return "; ".join(parts) + "."


def _trend_explanation(
    signal: str,
    close: float,
    short_ma: float | None,
    long_ma: float | None,
    recent_return: float | None,
    drawdown_worsening: bool,
) -> str:
    short_text = (
        f"50-day MA {short_ma:.4f}"
        if short_ma is not None
        else "50-day MA unavailable"
    )
    long_text = (
        f"200-day MA {long_ma:.4f}"
        if long_ma is not None
        else "200-day MA unavailable"
    )
    return_text = (
        f"1-month return {recent_return:.4f}"
        if recent_return is not None
        else "1-month return unavailable"
    )
    drawdown_text = (
        "drawdown is worsening"
        if drawdown_worsening
        else "drawdown is not worsening"
    )
    return (
        f"Trend is {signal}; close {close:.4f}, {short_text}, "
        f"{long_text}, {return_text}, {drawdown_text}."
    )


def _volatility_explanation(
    signal: str,
    vol20: float | None,
    vol60: float | None,
    vol252: float | None,
    percentile: float | None,
    risk_cap: float | None,
) -> str:
    details = [
        f"Volatility is {signal}",
        f"20-day {vol20:.4f}" if vol20 is not None else "20-day unavailable",
        f"60-day {vol60:.4f}" if vol60 is not None else "60-day unavailable",
        f"252-day {vol252:.4f}" if vol252 is not None else "252-day unavailable",
    ]
    if percentile is not None:
        details.append(f"percentile {percentile:.2f}")
    if risk_cap is not None:
        details.append(f"risk cap {risk_cap:.2f}")
    return "; ".join(details) + "."


def _select_equity_benchmark(
    prices: pd.DataFrame,
    equity_benchmark_ticker: str | None,
) -> pd.DataFrame | None:
    if equity_benchmark_ticker:
        ticker = equity_benchmark_ticker.upper()
        matches = prices[prices["ticker"].astype(str).str.upper() == ticker]
        if not matches.empty:
            return matches
    equities = prices[
        prices["asset_class_code"].astype(str).str.upper().isin({"EQUITY", "ETF"})
    ]
    if equities.empty:
        return None
    counts = equities.groupby(["security_id", "ticker", "asset_class_code"]).size()
    security_id, ticker, asset_class = counts.sort_values(ascending=False).index[0]
    return equities[
        (equities["security_id"] == security_id)
        & (equities["ticker"] == ticker)
        & (equities["asset_class_code"] == asset_class)
    ]


def security_statistics(
    prices: pd.DataFrame,
    *,
    short_window: int,
    long_window: int,
    momentum_window: int,
    volatility_window: int,
) -> pd.DataFrame:
    """Calculate trend, momentum, and annualized volatility per security."""
    rows: list[dict[str, object]] = []
    required = max(long_window, momentum_window + 1, volatility_window + 1)
    for (security_id, ticker, asset_class), group in prices.groupby(
        ["security_id", "ticker", "asset_class_code"],
        sort=True,
    ):
        closes = (
            group.sort_values("date")
            .drop_duplicates("date", keep="last")["close"]
            .astype(float)
        )
        if len(closes) < required:
            continue
        close_values = closes.to_numpy(dtype=np.float64)
        daily_returns = returnv(close_values)[-volatility_window:]
        recent_returns = daily_returns[np.isfinite(daily_returns)]
        volatility = float(np.std(recent_returns, ddof=1) * np.sqrt(TRADING_DAYS))
        momentum = float(returnv(close_values, momentum_window)[-1])
        rows.append(
            {
                "security_id": int(security_id),
                "ticker": str(ticker),
                "asset_class_code": str(asset_class),
                "trend_positive": bool(
                    closes.tail(short_window).mean()
                    > closes.tail(long_window).mean()
                ),
                "momentum_12m": momentum,
                "annualized_volatility": volatility,
            }
        )
    return pd.DataFrame(rows)


def aggregate_asset_class_signals(statistics: pd.DataFrame) -> tuple[AssetClassSignal, ...]:
    """Aggregate security indicators into strategy asset-class signals."""
    signals: list[AssetClassSignal] = []
    if statistics.empty:
        return ()
    for code, group in statistics.groupby("asset_class_code", sort=True):
        signals.append(
            AssetClassSignal(
                asset_class_code=str(code),
                trend_positive=bool(group["trend_positive"].any()),
                momentum_12m=float(group["momentum_12m"].mean()),
                annualized_volatility=float(
                    group["annualized_volatility"].replace([np.inf, -np.inf], np.nan).mean()
                ),
                security_count=len(group),
            )
        )
    return tuple(signals)


def signals_by_code(
    signals: Iterable[AssetClassSignal],
) -> dict[str, AssetClassSignal]:
    return {signal.asset_class_code: signal for signal in signals}


def security_signals_from_statistics(
    statistics: pd.DataFrame,
) -> tuple[SecuritySignal, ...]:
    return tuple(
        SecuritySignal(
            security_id=int(row.security_id),
            ticker=str(row.ticker),
            asset_class_code=str(row.asset_class_code),
            trend_positive=bool(row.trend_positive),
            momentum_12m=float(row.momentum_12m),
            annualized_volatility=float(row.annualized_volatility),
        )
        for row in statistics.itertuples(index=False)
    )
