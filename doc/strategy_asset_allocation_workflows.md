# Strategy and Asset Allocation Workflows

This guide explains how Stratos Quant turns portfolio data into deterministic
asset-class targets, advisory security selections, and reviewable rebalance
orders.

The most important boundary in the system is:

```text
Quantitative code decides allocation weights and trade sizes.
The local LLM explains those outputs and selects securities from validated inputs.
```

The application does not execute trades with a broker. Generated orders are
stored in SQLite for review, and the `Executed` flag is only an internal status
field.

## 1. End-to-end workflow

A complete dashboard analysis follows this sequence:

```text
1. Select an active portfolio and strategy model in the Gradio UI.
2. Reconstruct current holdings, cash, prices, and FX-adjusted values.
3. Load price history for eligible securities.
4. Compute trend, momentum, and volatility signals.
5. Run the selected allocation engine.
6. Persist the deterministic target asset-class weights.
7. Ask Ollama to explain the allocation.
8. For each positive non-cash target, ask Ollama to select candidate securities.
9. Validate and persist the selected security target weights.
10. Reconcile current holdings against target weights.
11. Persist BUY and SELL mandates with estimated trade values.
12. Display current allocation, targets, drift, rationale, and trades.
```

The orchestration entry point is `DashboardController.run_analysis()` in
`src/stratos_quant/ui/controller.py`.

## 2. Data used by the workflows

The allocation and rebalancing workflow reads existing portfolio data from
SQLite:

| Data | Used for |
|---|---|
| `accounts` and `portfolios` | Active portfolio selection and base currency |
| `transactions` | Holdings and cash reconstruction |
| `securities` | Tickers, security IDs, currencies, and asset classes |
| `price_history` | Strategy signals and latest holding values |
| `fx_rate_history` | Currency conversion into portfolio currency |
| `yahoo_fund_profiles` | Fund identity, net assets, and profile data |
| `yahoo_fund_metrics` | Expense ratios, risk metrics, and other KPIs |
| `yahoo_fund_performance` | Candidate performance history |

The workflow writes strategy state into:

| Table | Contents |
|---|---|
| `strategy_runs` | One analysis run, model name, Ollama model, and rationale |
| `strategy_target_allocations` | Deterministic target weights by asset class |
| `asset_recommendations` | Security-level recommendations and trade mandates |

Stratos Quant creates the strategy tables automatically if they are missing.

## 3. Asset-class source of truth

By default, asset classes come from `securities.asset_class`.

That same classification is used by:

- Price-history loading.
- Strategy signal aggregation.
- Candidate fund extraction.
- Current allocation reporting.
- Reconciliation and trade generation.

The controller can receive an optional `asset_class_map` that remaps tickers to
strategy-level asset classes. When present, this map is applied consistently
across allocation, current allocation reporting, fund extraction, and
reconciliation.

Use consistent codes such as `EQUITY`, `BOND`, `COMMODITY`, or `CASH`; the
strategy layer uppercases codes before using them.

## 4. Portfolio valuation workflow

`PortfolioValuationService.value_portfolio()` reconstructs portfolio state from
the transaction ledger.

### 4.1 Holdings

Holdings are calculated from transactions:

```text
net quantity = total BUY quantity - total SELL quantity
```

Only non-zero holdings are valued. Each holding is joined to the latest close on
or before the requested `as_of` date. If no date is supplied, the latest
available close is used.

### 4.2 Cash

Cash is replayed from the transaction ledger:

| Transaction type | Cash effect |
|---|---|
| `DEPOSIT` | Increases cash by value less fees |
| `WITHDRAWAL` | Decreases cash by value plus fees |
| `BUY` | Decreases cash by purchase value plus fees |
| `SELL` | Increases cash by sale value less fees |

Transaction-level currency exchange rates are applied when reconstructing cash.
This means cash quality depends on a complete ledger. Missing deposits, opening
balances, transfers, or borrowing semantics can produce negative cash.

### 4.3 FX conversion

Holdings are valued in the portfolio currency. FX rates are loaded from
`fx_rate_history`, and the service can use:

- Direct rates.
- Inverse rates.
- Multi-hop cross rates through connected currencies.

### 4.4 Strict and non-strict valuation

Strict valuation is used for order reconciliation. If a holding is missing a
price or FX path, the workflow raises an error instead of generating incomplete
trade values.

Non-strict valuation is used for LLM screening context so the model can still
see held security IDs even when some market data is incomplete.

## 5. Strategy preparation workflow

Both allocation engines inherit from `BaseAllocationEngine`.

The shared preparation path is:

```text
PriceHistoryLoader.load()
        |
        v
Long-form price DataFrame
        |
        v
security_statistics()
        |
        v
Per-security trend, momentum, volatility
        |
        v
aggregate_asset_class_signals()
        |
        v
AssetClassSignal records
```

### 5.1 Price loading

`PriceHistoryLoader` reads positive closes from `price_history` joined to
`securities`.

It supports:

- Optional security ID filtering.
- Optional `as_of` filtering.
- Optional ticker-to-asset-class remapping.

The resulting data has these columns:

```text
security_id, ticker, asset_class_code, date, close
```

### 5.2 Signal calculation

For each security with enough price observations, the strategy layer calculates:

| Signal | Default window | Meaning |
|---|---:|---|
| Short moving average | 50 trading days | Recent trend baseline |
| Long moving average | 200 trading days | Long trend baseline |
| Momentum | 252 trading days | Approximate 12-month return |
| Volatility | 63 trading days | Recent annualized volatility |

A security is considered to have a positive trend when:

```text
50-day average close > 200-day average close
```

Momentum is calculated with PyBroker's `returnv()` function. Volatility is the
standard deviation of recent daily returns multiplied by the square root of 252.

### 5.3 Asset-class aggregation

Security signals are aggregated by `asset_class_code`.

An asset class has:

- `trend_positive`: true when at least one security in the class has a positive
  trend.
- `momentum_12m`: the average 12-month momentum of included securities.
- `annualized_volatility`: the average annualized volatility of included
  securities.
- `security_count`: the number of securities contributing to the class.

If no security has enough history for the configured windows, the allocation
engine raises `InsufficientPriceHistoryError`.

## 6. Hierarchical strategy workflow

`HierarchicalAllocationEngine` is a decision-tree allocator. It is designed to
pick one winning risk asset class and optionally hold the remaining weight in
`CASH`.

### 6.1 Decision path

```text
Start with asset-class signals
        |
        v
Keep classes with positive 50/200 trend
        |
        v
Keep classes with positive 12-month momentum
        |
        v
If no class remains, allocate 100% to CASH
        |
        v
Select the highest-momentum class
        |
        v
Break ties by lower volatility, then asset-class code
        |
        v
Scale risk allocation by target volatility
        |
        v
Put unused weight into CASH
```

### 6.2 Volatility sizing

The engine has a `target_volatility` parameter, defaulting to `0.15`.

If the winning asset class has annualized volatility, the risk weight is:

```text
risk weight = min(1.0, target volatility / winner volatility)
```

Examples:

- If target volatility is `0.15` and winner volatility is `0.10`, the class
  receives `1.0` because the strategy does not lever above 100%.
- If target volatility is `0.15` and winner volatility is `0.30`, the class
  receives `0.5` and `CASH` receives the remaining `0.5`.

If the winner is already `CASH`, the target remains 100% cash.

### 6.3 Output

The result model name is `HIERARCHICAL`. The result includes:

- Final target weights.
- Asset-class signals.
- Per-security signals.

The final weights are normalized to exactly `1.0000000000`.

## 7. Ensemble strategy workflow

`EnsembleAllocationEngine` blends three independent sub-strategies:

```text
1. moving_average
2. dual_momentum
3. volatility_scaler
```

The sub-strategies run in parallel using `ThreadPoolExecutor`.

### 7.1 Moving-average component

The moving-average component gives equal weight to every asset class with a
positive trend.

If no asset class has a positive trend, this component allocates 100% to
`CASH`.

Example:

```text
Positive trend: EQUITY, BOND
Component target: EQUITY 50%, BOND 50%
```

### 7.2 Dual-momentum component

The dual-momentum component keeps asset classes with:

- Positive trend.
- Positive 12-month momentum.

It gives 100% of its component weight to the highest-momentum class. If no class
qualifies, it allocates 100% to `CASH`.

### 7.3 Volatility-scaler component

The volatility-scaler component weights classes by inverse volatility:

```text
raw weight = 1 / annualized volatility
```

Lower-volatility classes receive larger weights. Classes with missing or
non-positive volatility are excluded from this component. If no class has usable
volatility, the component allocates 100% to `CASH`.

### 7.4 Component blend

By default, the three components receive equal blend weights. The default raw
blend is:

```text
moving_average: 1
dual_momentum: 1
volatility_scaler: 1
```

After normalization, each component contributes one third of the final target.

A custom `component_blend` may be supplied, but it must define all three
components:

```python
{
    "moving_average": 2,
    "dual_momentum": 1,
    "volatility_scaler": 1,
}
```

The final result is the weighted sum of all component allocations, normalized to
exactly `1.0000000000`.

### 7.5 Output

The result model name is `ENSEMBLE`. The result includes:

- Final target weights.
- Asset-class signals.
- Per-security signals.
- Component weights for each sub-strategy.

Component weights are shown in the dashboard so the user can inspect how the
final blend was formed.

## 8. Target-weight normalization

All strategy weights are normalized by `normalize_weights()`.

The normalizer:

1. Drops zero and negative weights.
2. Converts values to `Decimal`.
3. Sorts asset-class codes for deterministic output.
4. Quantizes each weight to ten decimal places.
5. Assigns the final rounding remainder to the last sorted asset class.

The invariant is:

```text
sum(weights) == 1.0000000000
```

This exact sum is required before targets can be persisted or reconciled.

## 9. Advisory workflow

The advisory layer is implemented by `AdvisoryPipeline`.

It has two jobs:

1. Ask Ollama to explain the deterministic allocation.
2. Ask Ollama to select securities for each positive non-cash target.

It does not allow Ollama to change the allocation math.

### 9.1 Allocation rationale

`rationalize_allocation()` serializes the full `AllocationResult`, including:

- Model name.
- As-of date.
- Final target weights.
- Asset-class signals.
- Per-security signals.
- Ensemble component weights when present.

Ollama receives instructions to explain the supplied evidence only. After a
successful response, the repository creates:

- One row in `strategy_runs`.
- One row per asset class in `strategy_target_allocations`.

### 9.2 Candidate screening

For each target asset class where:

```text
target weight > 0 and asset class != CASH
```

the controller calls `screen_portfolio_asset_class()`.

This extracts:

- Candidate securities for the asset class.
- Fund profiles.
- Expense ratios.
- Net assets.
- Risk metrics.
- Performance history.
- Current held security IDs.

Ollama must return JSON recommendations containing:

- `security_id`
- `ticker`
- `action_type`
- `target_weight`
- `rationale`

### 9.3 Advisory validation

Before anything is persisted, the pipeline validates that:

- The response contains at least one recommendation.
- Every recommendation parses into a `SecurityRecommendation`.
- Every security ID appears in the candidate context.
- Every ticker matches the candidate security ID.
- Every action is one of `BUY`, `SELL`, or `HOLD`.
- Recommendation target weights sum exactly to the deterministic target weight.

If validation fails, the advisory stage raises an error and the dashboard shows
the deterministic allocation tables without a complete order set.

## 10. Reconciliation workflow

`ReconciliationService.reconcile()` converts asset-class targets and validated
security selections into trade mandates.

### 10.1 Inputs

Reconciliation requires:

- A persisted strategy run.
- Persisted target allocations.
- Current strict portfolio valuation.
- Unexecuted security recommendations.
- A drift threshold.

The default drift threshold in the dashboard controller is `0.01`, meaning 1%
of portfolio value.

### 10.2 Asset-class drift

For each asset class that appears in either current holdings or target weights:

```text
target value = portfolio total value * target weight
drift value  = target value - current value
drift weight = drift value / portfolio total value
```

If:

```text
abs(drift weight) < drift threshold
```

the asset class is marked suppressed and generates no orders.

`CASH` is included in drift reporting but skipped for security mandate
generation. Cash is the balancing leg, not a tradable security.

### 10.3 Security-level trade sizing

For each non-suppressed, non-cash asset class:

1. Current holdings are grouped by security.
2. Unexecuted selected recommendations are loaded.
3. Desired security weights are taken from the recommendations.
4. Desired security values are calculated:

   ```text
   desired value = portfolio total value * security target weight
   ```

5. Trade values are calculated:

   ```text
   trade value = desired value - current security value
   ```

6. Positive trade values become `BUY`.
7. Negative trade values become `SELL`.
8. Stored trade values are positive magnitudes, with direction kept in
   `action_type`.

Current holdings that are not selected by Ollama receive a desired weight of
zero and therefore generate SELL mandates when the drift is not suppressed.

### 10.4 Recommendation weight safety

For a positive asset-class target, reconciliation requires the selected security
weights for that class to equal the target asset-class weight exactly.

For example:

```text
EQUITY target = 0.7000000000
Selected SPY target = 0.4000000000
Selected VTI target = 0.3000000000
Valid total = 0.7000000000
```

If the selected security weights do not equal the target, reconciliation fails
instead of generating inconsistent orders.

### 10.5 Persistence behavior

When a mandate maps to an existing Ollama-selected recommendation,
reconciliation updates that row with:

- Final `action_type`.
- Final `target_weight`.
- Final `estimated_trade_value`.

When a mandate is needed for a currently held security that Ollama did not
select, reconciliation inserts a new SELL recommendation with a deterministic
rationale.

Executed recommendations are excluded from later reconciliation inputs.

## 11. Dashboard workflow

The dashboard presents the full pipeline through one action button.

### 11.1 User inputs

The dashboard asks for:

- Active portfolio.
- Allocation model: `Hierarchical` or `Ensemble`.

Portfolio choices are loaded from active accounts and active portfolios.

### 11.2 Output panels

After a run, the dashboard displays:

- Current allocation summary.
- Target allocation and drift.
- Strategy KPI table.
- Ensemble component weights, when available.
- Ollama strategy rationale.
- Generated recommendations and estimated trade values.
- Run status.

Only the `Executed` column in the recommendation table is editable.

### 11.3 Ledger warning path

If reconstructed cash is negative, the controller still shows allocation output
and the Ollama rationale, but it disables reconciliation.

This avoids generating executable orders from a ledger that may be missing
deposits, opening balances, transfers, or borrowing semantics.

### 11.4 Partial failure path

The controller separates deterministic allocation from advisory and
reconciliation failures.

If allocation fails, no downstream work runs.

If allocation succeeds but advisory or reconciliation fails, the dashboard still
shows:

- Current allocation.
- Target allocation.
- Strategy KPIs.
- Component weights when available.
- The failure message.

This lets the user inspect the deterministic model output even when order
generation did not complete.

## 12. Common failure modes

| Failure | Cause | Result |
|---|---|---|
| `InsufficientPriceHistoryError` | No security has enough observations for the configured windows | Allocation stops |
| Missing market data | Strict valuation finds missing price or FX route | Reconciliation stops |
| Negative cash warning | Ledger cash is below zero | Allocation is shown, orders are blocked |
| Invalid Ollama rationale | Empty or failed local model response | Run persistence stops |
| Invalid recommendation | Unknown security, mismatched ticker, malformed action, or bad weight total | Screening stops |
| Target weights do not sum to one | Corrupt or manually changed target rows | Reconciliation stops |
| No selected securities for positive target | LLM did not provide eligible recommendations | Reconciliation stops |

## 13. Programmatic examples

### 13.1 Run the Ensemble allocator

```python
from stratos_quant.strategy import EnsembleAllocationEngine

allocation = EnsembleAllocationEngine().run()
print(allocation.to_dict()["weights"])
```

### 13.2 Run the Hierarchical allocator as of a date

```python
from datetime import date

from stratos_quant.strategy import HierarchicalAllocationEngine

allocation = HierarchicalAllocationEngine().run(as_of=date(2026, 6, 29))
print(allocation.model)
print(allocation.weights)
```

### 13.3 Use a custom Ensemble blend

```python
from stratos_quant.strategy import EnsembleAllocationEngine

engine = EnsembleAllocationEngine(
    component_blend={
        "moving_average": 2,
        "dual_momentum": 1,
        "volatility_scaler": 1,
    }
)
allocation = engine.run()
```

### 13.4 Reconcile a persisted run

```python
from decimal import Decimal

from stratos_quant.db import create_sqlite_engine
from stratos_quant.reconciliation import ReconciliationService

engine = create_sqlite_engine()
result = ReconciliationService(engine).reconcile(
    run_id=123,
    portfolio_id=21,
    drift_threshold=Decimal("0.01"),
)

for mandate in result.mandates:
    print(mandate.action_type, mandate.ticker, mandate.estimated_trade_value)
```

## 14. Extension points

### Add a new allocation engine

To add another strategy:

1. Subclass `BaseAllocationEngine`.
2. Implement `allocate(prices, as_of=None)`.
3. Call `self._prepare(prices, as_of)` to reuse loading and signal logic.
4. Return an `AllocationResult` with normalized weights.
5. Add the engine to `DashboardController.engines`.
6. Add focused tests for success, defensive fallback, and insufficient history.

### Change signal windows

The base engine constructor supports:

- `short_window`
- `long_window`
- `momentum_window`
- `volatility_window`
- `defensive_asset_class`

Changing these values changes the amount of history required for a security to
be included in the strategy calculation.

### Change drift policy

The dashboard controller accepts `drift_threshold`. Raising the threshold
reduces small rebalance orders. Lowering it makes the system more sensitive to
small deviations from target weights.

### Change candidate universe

The candidate universe comes from securities and Yahoo fund data for each asset
class. To change what Ollama can select, update the underlying securities,
asset-class codes, and fund data. The model cannot select securities outside the
candidate context.

## 16. Research and optimization workflows

These workflows are used for offline evaluation and performance monitoring rather than real-time dashboard analysis.

### 16.1 Backtesting workflow

The `BacktestEngine` allows testing strategy logic against historical price data without reconstructing full portfolio transaction ledgers.

```text
1. Define a set of securities and their asset classes.
2. Load historical prices for the target period.
3. Execute simulation with specific allocation engine (Hierarchical/Ensemble).
4. Calculate equity curves, drawdowns, and risk-adjusted returns.
5. Output results as a performance report or persist to SQLite.
```

### 16.2 Performance and ranking workflow

This workflow ensures that the strategy's historical behavior is measured and compared against goals.

1. **KPI Calculation**: The `KPIEngine` processes historical `strategy_runs` and compares them against `price_history` and `asset_recommendations`. It calculates metrics like Sharpe ratio, max drawdown, and tracking error.
2. **Goal-based Ranking**: The `RankingService` ranks strategies by how well they adhered to target allocations or achieved performance goals. 
3. **Asset Selection Optimization**: Using the ranking results, users can identify which asset classes or specific securities are consistently driving alpha or contributing most to volatility.

These metrics and rankings are persisted in SQLite, enabling longitudinal analysis of strategy evolution over time.

