from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from .base import BaseAllocationEngine
from .models import AllocationResult, normalize_weights
from .signals import security_signals_from_statistics


class HierarchicalAllocationEngine(BaseAllocationEngine):
    """Decision-tree allocator using trend, momentum, and volatility sizing."""

    def __init__(
        self,
        *args,
        target_volatility: float = 0.15,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        if target_volatility <= 0:
            raise ValueError("target_volatility must be positive")
        self.target_volatility = target_volatility

    def allocate(
        self,
        prices: pd.DataFrame,
        *,
        as_of: date | None = None,
    ) -> AllocationResult:
        statistics, signals, resolved_as_of = self._prepare(prices, as_of)
        eligible = [
            signal
            for signal in signals
            if signal.trend_positive
            and signal.momentum_12m is not None
            and signal.momentum_12m > 0
        ]
        if not eligible:
            weights = normalize_weights({self.defensive_asset_class: Decimal("1")})
        else:
            winner = max(
                eligible,
                key=lambda signal: (
                    signal.momentum_12m,
                    -(
                        signal.annualized_volatility
                        if signal.annualized_volatility is not None
                        else float("inf")
                    ),
                    signal.asset_class_code,
                ),
            )
            volatility = winner.annualized_volatility or 0.0
            if winner.asset_class_code == self.defensive_asset_class:
                raw_weights = {self.defensive_asset_class: 1.0}
            else:
                risk_weight = (
                    min(1.0, self.target_volatility / volatility)
                    if volatility > 0
                    else 1.0
                )
                raw_weights = {winner.asset_class_code: risk_weight}
                if risk_weight < 1.0:
                    raw_weights[self.defensive_asset_class] = 1.0 - risk_weight
            weights = normalize_weights(raw_weights)

        return AllocationResult(
            model="HIERARCHICAL",
            as_of=resolved_as_of,
            weights=weights,
            signals=signals,
            security_signals=security_signals_from_statistics(statistics),
        )
