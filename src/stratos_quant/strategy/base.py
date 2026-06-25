from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

import pandas as pd

from .errors import InsufficientPriceHistoryError
from .market_data import PriceHistoryLoader
from .models import AssetClassSignal
from .signals import aggregate_asset_class_signals, security_statistics


class BaseAllocationEngine:
    """Shared market-data preparation for allocation engines."""

    def __init__(
        self,
        loader: PriceHistoryLoader | None = None,
        *,
        short_window: int = 50,
        long_window: int = 200,
        momentum_window: int = 252,
        volatility_window: int = 63,
        defensive_asset_class: str = "CASH",
    ) -> None:
        if not 0 < short_window < long_window:
            raise ValueError("short_window must be positive and below long_window")
        if momentum_window <= 0 or volatility_window <= 1:
            raise ValueError("Momentum and volatility windows must be positive")
        self._loader = loader or PriceHistoryLoader()
        self.short_window = short_window
        self.long_window = long_window
        self.momentum_window = momentum_window
        self.volatility_window = volatility_window
        self.defensive_asset_class = defensive_asset_class.strip().upper()

    def run(
        self,
        *,
        security_ids: Sequence[int] | None = None,
        as_of: date | None = None,
        asset_class_map: Mapping[str, str] | None = None,
    ):
        prices = self._loader.load(
            security_ids=security_ids,
            as_of=as_of,
            asset_class_map=asset_class_map,
        )
        return self.allocate(prices, as_of=as_of)

    def allocate(self, prices: pd.DataFrame, *, as_of: date | None = None):
        raise NotImplementedError

    def _prepare(
        self,
        prices: pd.DataFrame,
        as_of: date | None,
    ) -> tuple[pd.DataFrame, tuple[AssetClassSignal, ...], date]:
        if prices.empty:
            raise InsufficientPriceHistoryError("No price history was supplied")
        statistics = security_statistics(
            prices,
            short_window=self.short_window,
            long_window=self.long_window,
            momentum_window=self.momentum_window,
            volatility_window=self.volatility_window,
        )
        if statistics.empty:
            required = max(
                self.long_window,
                self.momentum_window + 1,
                self.volatility_window + 1,
            )
            raise InsufficientPriceHistoryError(
                f"No security has the required {required} price observations"
            )
        resolved_as_of = as_of or pd.Timestamp(prices["date"].max()).date()
        return statistics, aggregate_asset_class_signals(statistics), resolved_as_of
