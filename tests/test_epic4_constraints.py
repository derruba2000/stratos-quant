from __future__ import annotations

from decimal import Decimal

import pytest

from stratos_quant.strategy import (
    AllocationConstraintError,
    AllocationConstraints,
    PortfolioRebalanceEngine,
    apply_allocation_constraints,
)
from stratos_quant.strategy.models import ONE


def test_rebalance_caps_single_asset_and_keeps_total_at_one_with_cash_residual():
    result = apply_allocation_constraints(
        {"SPY": Decimal("0.92"), "AGG": Decimal("0.08")},
        AllocationConstraints(
            max_single_asset_weight=Decimal("0.60"),
            min_cash_weight=Decimal("0.10"),
        ),
        rankings=["SPY", "AGG"],
    )

    assert sum(result.weights.values()) == ONE
    assert result.weights["SPY"] == Decimal("0.6000000000")
    assert result.weights["AGG"] == Decimal("0.3000000000")
    assert result.weights["CASH"] == Decimal("0.1000000000")
    assert all(weight <= Decimal("0.60") for weight in result.weights.values())
    assert {item.constraint_name for item in result.adjustments} >= {
        "minimum_weight",
        "max_single_asset_weight",
    }
    assert "redistributed" in result.explanation


def test_rebalance_applies_asset_class_caps_without_creating_violations():
    result = PortfolioRebalanceEngine(
        AllocationConstraints(
            max_single_asset_weight=Decimal("0.50"),
            max_asset_class_weights={"EQUITY": Decimal("0.55")},
            min_cash_weight=Decimal("0.05"),
        )
    ).rebalance(
        {
            "SPY": Decimal("0.50"),
            "QQQ": Decimal("0.25"),
            "AGG": Decimal("0.20"),
            "CASH": Decimal("0.05"),
        },
        asset_class_map={
            "SPY": "EQUITY",
            "QQQ": "EQUITY",
            "AGG": "BOND",
            "CASH": "CASH",
        },
        rankings=["SPY", "QQQ", "AGG", "CASH"],
    )

    assert sum(result.weights.values()) == ONE
    assert result.weights["SPY"] + result.weights["QQQ"] == Decimal("0.5500000000")
    assert result.weights["AGG"] == Decimal("0.4000000000")
    assert result.weights["CASH"] == Decimal("0.0500000000")
    assert any(
        adjustment.constraint_name == "max_asset_class_weight"
        and adjustment.code == "EQUITY"
        for adjustment in result.adjustments
    )


def test_rebalance_raises_when_hard_constraints_are_impossible():
    with pytest.raises(AllocationConstraintError, match="Minimum allocation"):
        apply_allocation_constraints(
            {"SPY": Decimal("0.50"), "AGG": Decimal("0.50")},
            AllocationConstraints(
                min_single_asset_weight=Decimal("0.60"),
                min_cash_weight=Decimal("0.10"),
            ),
        )


def test_soft_constraints_emit_warning_but_do_not_force_adjustment():
    result = apply_allocation_constraints(
        {"BTC": Decimal("0.30"), "SPY": Decimal("0.70")},
        AllocationConstraints(
            soft_max_asset_class_weights={"CRYPTO": Decimal("0.10")},
        ),
        asset_class_map={"BTC": "CRYPTO", "SPY": "EQUITY"},
    )

    assert result.weights["BTC"] == Decimal("0.3000000000")
    assert result.adjustments == ()
    assert result.warnings == (
        "Soft max asset-class weight breached by CRYPTO: 0.3000000000.",
    )
