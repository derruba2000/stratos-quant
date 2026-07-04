# Plan for Epic 4: Portfolio-Level Allocation Engine

## Overview
Implement the core logic for combining asset-class level allocations with security-level allocations, applying constraints, and managing drift bands.

## Phase 1: Hierarchical Weight Combination (Use Case 4.1)
* [ ] Create `HierarchicalAllocationEngine` that takes both asset class weights and security weights as inputs.
* [ ] Implement the logic to multiply asset class weight by the relative security weight within that class.
* [ ] Ensure final weights sum to exactly 100% using `normalize_weights`.

## Phase 2: Constraint Enforcement (Use Case 4.4)
* [ ] Define a set of constraint types: `MaxSingleAssetWeight`, `MaxAssetClassWeight`, `MinCashWeight`, etc.
* [ ] Implement a `ConstraintEnforcer` class or method that adjusts weights to respect these constraints.
* [ ] Support both hard and soft constraints (with warnings).

## Phase 3: Drift Bands and Rebalancing Logic (Use Case 4.2 & 4.3)
* [ ] Implement drift calculation: `drift = current_weight - target_weight`.
* [ ] Define `DriftBand` configuration for assets/asset classes (min/max absolute or relative).
* [ ] Create a mechanism to identify which assets have breached their bands.
* [ ] Implement weight redistribution logic (Use Case 4.3) so that when an asset is reduced, the weight is reallocated correctly (e.g., to cash or higher-ranked assets).

## Phase 4: Integration and Testing
* [ ] Integrate the new engine into the backtesting workflow.
* [ ] Write unit tests for hierarchical combination, constraint enforcement, and drift detection.
* [ ] Verify that final target weights always sum to 100% in all scenarios.

# Epic 5: Rebalance Decision Engine

## Overview
Implement logic to decide when and how to rebalance a portfolio based on drift, strategy signals, costs, and schedules.

## Phase 1: Drift Detection (Use Case 5.1)
* [ ] Implement `DriftDetector` to compare current weights vs target weights.
* [ ] Support both absolute and relative drift calculations.
* [ ] Identify assets/asset-classes breaching their allowed bands.

## Phase 2: Trade Generation (Use Case 5.2)
* [ ] Create a mechanism to generate buy/sell orders to bring weights back within bands.
* [ ] Calculate target values, trade values, and estimated quantities for each asset.
* [ ] Ensure total allocation after trades equals 100%.

## Phase 3: Cost-Aware Rebalancing (Use Case 5.3)
* [ ] Integrate estimation of broker fees and slippage into the decision process.
* [ ] Implement logic to skip rebalancing if `net_expected_benefit < cost`.

## Phase 4: Scheduling & Execution (Use Case 5.4)
* [ ] Support periodic (weekly/monthly) rebalance triggers.
* [ ] Integrate drift-based and regime-change-based triggers.
