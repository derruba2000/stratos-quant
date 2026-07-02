"""Quantitative allocation engines."""

from .constraints import (
    AllocationConstraints,
    ConstraintAdjustment,
    ConstrainedAllocationResult,
    PortfolioRebalanceEngine,
    apply_allocation_constraints,
)
from .ensemble import EnsembleAllocationEngine
from .errors import (
    AllocationConstraintError,
    InsufficientPriceHistoryError,
    StrategyError,
)
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
    "AllocationConstraintError",
    "AllocationConstraints",
    "AssetClassSignal",
    "ConstrainedAllocationResult",
    "ConstraintAdjustment",
    "EnsembleAllocationEngine",
    "HierarchicalAllocationEngine",
    "InsufficientPriceHistoryError",
    "MarketRegimeSignal",
    "MomentumSignal",
    "PriceHistoryLoader",
    "PortfolioRebalanceEngine",
    "SecuritySignal",
    "SignalGenerationResult",
    "StrategyError",
    "TrendSignal",
    "VolatilitySignal",
    "apply_allocation_constraints",
    "generate_market_regime_signal",
    "generate_momentum_signals",
    "generate_signal_suite",
    "generate_trend_signals",
    "generate_volatility_signals",
]
