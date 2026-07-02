"""Quantitative allocation engines."""

from .ensemble import EnsembleAllocationEngine
from .errors import InsufficientPriceHistoryError, StrategyError
from .hierarchical import HierarchicalAllocationEngine
from .market_data import PriceHistoryLoader
from .models import (
    AllocationResult,
    AssetClassSignal,
    MarketRegimeSignal,
    MomentumSignal,
    SecuritySignal,
    SignalGenerationResult,
    TrendSignal,
    VolatilitySignal,
)
from .signals import (
    generate_market_regime_signal,
    generate_momentum_signals,
    generate_signal_suite,
    generate_trend_signals,
    generate_volatility_signals,
)

__all__ = [
    "AllocationResult",
    "AssetClassSignal",
    "EnsembleAllocationEngine",
    "HierarchicalAllocationEngine",
    "InsufficientPriceHistoryError",
    "MarketRegimeSignal",
    "MomentumSignal",
    "PriceHistoryLoader",
    "SecuritySignal",
    "SignalGenerationResult",
    "StrategyError",
    "TrendSignal",
    "VolatilitySignal",
    "generate_market_regime_signal",
    "generate_momentum_signals",
    "generate_signal_suite",
    "generate_trend_signals",
    "generate_volatility_signals",
]
