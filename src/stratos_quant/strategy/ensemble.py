from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from typing import Callable, Mapping

import pandas as pd

from .base import BaseAllocationEngine
from .models import AllocationResult, normalize_weights
from .signals import signals_by_code


class EnsembleAllocationEngine(BaseAllocationEngine):
    """Blend moving-average, dual-momentum, and volatility sub-portfolios."""

    COMPONENTS = ("moving_average", "dual_momentum", "volatility_scaler")

    def __init__(
        self,
        *args,
        component_blend: Mapping[str, float | Decimal] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        blend = component_blend or {
            component: Decimal("1") for component in self.COMPONENTS
        }
        if set(blend) != set(self.COMPONENTS):
            raise ValueError(
                "component_blend must define moving_average, dual_momentum, "
                "and volatility_scaler"
            )
        self.component_blend = normalize_weights(blend)

    def allocate(
        self,
        prices: pd.DataFrame,
        *,
        as_of: date | None = None,
    ) -> AllocationResult:
        _, signals, resolved_as_of = self._prepare(prices, as_of)
        signal_map = signals_by_code(signals)
        strategies: dict[str, Callable[[], dict[str, Decimal]]] = {
            "moving_average": lambda: self._moving_average_vote(signal_map),
            "dual_momentum": lambda: self._dual_momentum_vote(signal_map),
            "volatility_scaler": lambda: self._volatility_vote(signal_map),
        }
        with ThreadPoolExecutor(max_workers=len(strategies)) as executor:
            futures = {
                name: executor.submit(strategy)
                for name, strategy in strategies.items()
            }
            component_weights = {
                name: futures[name].result()
                for name in self.COMPONENTS
            }

        blended: dict[str, Decimal] = {}
        for component, weights in component_weights.items():
            component_share = self.component_blend[component]
            for asset_class, weight in weights.items():
                blended[asset_class] = (
                    blended.get(asset_class, Decimal("0"))
                    + component_share * weight
                )

        return AllocationResult(
            model="ENSEMBLE",
            as_of=resolved_as_of,
            weights=normalize_weights(blended),
            signals=signals,
            component_weights=component_weights,
        )

    def _moving_average_vote(self, signals) -> dict[str, Decimal]:
        positive = {
            code: Decimal("1")
            for code, signal in signals.items()
            if signal.trend_positive
        }
        return normalize_weights(
            positive or {self.defensive_asset_class: Decimal("1")}
        )

    def _dual_momentum_vote(self, signals) -> dict[str, Decimal]:
        eligible = [
            signal
            for signal in signals.values()
            if signal.trend_positive
            and signal.momentum_12m is not None
            and signal.momentum_12m > 0
        ]
        if not eligible:
            return normalize_weights({self.defensive_asset_class: Decimal("1")})
        winner = max(
            eligible,
            key=lambda signal: (signal.momentum_12m, signal.asset_class_code),
        )
        return normalize_weights({winner.asset_class_code: Decimal("1")})

    def _volatility_vote(self, signals) -> dict[str, Decimal]:
        inverse_volatility = {
            code: Decimal("1") / Decimal(str(signal.annualized_volatility))
            for code, signal in signals.items()
            if signal.annualized_volatility is not None
            and signal.annualized_volatility > 0
        }
        return normalize_weights(
            inverse_volatility
            or {self.defensive_asset_class: Decimal("1")}
        )
