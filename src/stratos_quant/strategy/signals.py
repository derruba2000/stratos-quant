from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd
from pybroker import returnv

from .models import AssetClassSignal, SecuritySignal


TRADING_DAYS = 252


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
