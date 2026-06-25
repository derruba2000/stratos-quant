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
   - `OLLAMA_BASE_URL` to the local Ollama API endpoint.

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

## Ollama advisory pipeline

Persist an allocation rationale:

```python
from stratos_quant.db import create_sqlite_engine
from stratos_quant.llm import AdvisoryPipeline, OllamaClient, StrategyRepository
from stratos_quant.strategy import HierarchicalAllocationEngine

engine = create_sqlite_engine()
allocation = HierarchicalAllocationEngine().run()
pipeline = AdvisoryPipeline(
    OllamaClient(),
    StrategyRepository(engine),
)
run_id = pipeline.rationalize_allocation(
    portfolio_id=21,
    allocation=allocation,
)
```

Screen candidates using Yahoo fundamentals:

```python
from decimal import Decimal

from stratos_quant.data import FundDataExtractor

candidates = FundDataExtractor(engine).extract_asset_class("ETF")
recommendations = pipeline.screen_asset_class(
    run_id=run_id,
    portfolio_id=21,
    asset_class_code="ETF",
    target_weight=Decimal("1.0000000000"),
    candidate_context=candidates,
    held_security_ids={1, 2, 3},
)
```

`StrategyRepository` creates the Epic 4 tables when needed. Ollama responses
are validated against candidate security IDs and exact target weights before
anything is written. Trade values remain zero until Epic 5 performs portfolio
reconciliation.
