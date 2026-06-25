# Stratos Quant

Core infrastructure for the Stratos-Quant portfolio rebalancing system.

## Quick Start

1. Select Python 3.12 and install the locked dependencies:
   ```bash
   poetry env use 3.12
   poetry install
   ```
2. Create a `.env` file based on `.env.example` and set:

   - `SQLITE_DB_PATH` to an existing SQLite database file.
   - `OLLAMA_MODEL` to the local model name used for inference.

3. Run tests:
   ```bash
   poetry run pytest
   ```

The PyPI distribution for the `pybroker` import is named `lib-pybroker`, which
is why that name appears in `pyproject.toml`.

## Data extraction

Reconstruct a portfolio in its account currency:

```python
from stratos_quant.data import PortfolioValuationService

valuation = PortfolioValuationService().value_portfolio(portfolio_id=21)
print(valuation.total_value, valuation.currency)
```

Extract Yahoo profile, metric, and performance context by asset class:

```python
from stratos_quant.data import FundDataExtractor

context_json = FundDataExtractor().extract_asset_class_json("ETF", indent=2)
```

Portfolio valuation raises `MissingMarketDataError` when a held security has no
price or usable FX route. Pass `strict=False` to return an explicitly incomplete
valuation instead.

## Allocation engines

Run the hierarchical trend/momentum allocator:

```python
from stratos_quant.strategy import HierarchicalAllocationEngine

result = HierarchicalAllocationEngine().run(
    asset_class_map={
        "VWRP.L": "GLOBAL_EQUITY",
        "IGLS.L": "BONDS",
        "XEON.DE": "CASH_EQUIVALENT",
    }
)
print(result.to_dict()["weights"])
```

Run the equally blended moving-average, dual-momentum, and inverse-volatility
ensemble:

```python
from stratos_quant.strategy import EnsembleAllocationEngine

result = EnsembleAllocationEngine().run(
    asset_class_map={"VWRP.L": "GLOBAL_EQUITY", "IGLS.L": "BONDS"}
)
```

Both engines read `price_history`, use PyBroker's vectorized return calculation,
and output weights quantized to ten decimal places that sum to exactly
`1.0000000000`. Securities without enough observations for the 50/200-day trend
and 12-month momentum windows are excluded. The optional `asset_class_map`
overrides the broad classification stored in `securities.asset_class`.
