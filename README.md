# Stratos Quant

Stratos Quant is a local portfolio analysis and rebalancing application. It
reconstructs portfolio state from SQLite, produces deterministic allocation
targets, asks a local Ollama model to explain and screen those targets, and
persists proposed rebalance orders for review in a Gradio dashboard.

The application does **not** connect to a broker or execute trades. Marking a
recommendation as executed only updates its status in SQLite.

Detailed component and interaction documentation is available in
[doc/architecture.md](doc/architecture.md).

A comprehensive strategy and asset-allocation workflow guide is available in
[doc/strategy_asset_allocation_workflows.md](doc/strategy_asset_allocation_workflows.md).

## What is implemented

- Portfolio holdings and cash reconstruction from the transaction ledger.
- Latest-price valuation with direct, inverse, and cross-currency FX conversion.
- Yahoo fund profile, expense, metric, and performance extraction.
- Hierarchical trend, momentum, and volatility allocation engine.
- Parallel ensemble of moving-average, dual-momentum, and volatility strategies.
- Exact ten-decimal target-weight normalization.
- Local Ollama rationale generation and fund screening.
- Asset-class drift calculation and configurable rebalance suppression.
- Persisted BUY, SELL, and HOLD recommendations with estimated trade values.
- Gradio control board with portfolio/model selection and execution-status edits.

## System flow

```text
SQLite portfolio data
        |
        v
Holdings, cash, prices, FX and fund KPIs
        |
        v
Hierarchical or Ensemble allocation engine
        |
        v
Target asset-class weights
        |
        v
Ollama rationale and ticker screening
        |
        v
Current-versus-target reconciliation
        |
        v
SQLite recommendations and Gradio review
```

## Requirements

- Python 3.12 or a compatible Python version allowed by `^3.12`.
- Poetry.
- An existing SQLite portfolio database using the schema described below.
- Either a running local [Ollama](https://ollama.com/) server or NVIDIA API
  credentials.

The PyPI package that provides the `pybroker` import is named
`lib-pybroker`.

## Run The App

Install dependencies:

```bash
poetry env use 3.12
poetry install
cp .env.example .env
```

Configure `.env` for Ollama:

```dotenv
SQLITE_DB_PATH=/absolute/path/to/portfolio_management.sqlite3
API_USAGE=ollama
OLLAMA_MODEL=gemma4
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_TIMEOUT_SECONDS=300
```

Confirm that Ollama is running and the configured model exists:

```bash
ollama list
curl http://localhost:11434/api/tags
```

Or configure `.env` for NVIDIA:

```dotenv
SQLITE_DB_PATH=/absolute/path/to/portfolio_management.sqlite3
API_USAGE=NVIDIA
NVIDIA_API_MODEL=meta/llama-3.3-70b-instruct
NVIDIA_API_KEY=your-api-key
OLLAMA_TIMEOUT_SECONDS=300
NVIDIA_TIMEOUT_SECONDS=900
# Optional, only when your network requires a custom CA bundle:
# NVIDIA_CA_BUNDLE=/absolute/path/to/corporate-ca.pem
# Local/dev fallback for certificate interception issues:
# NVIDIA_VERIFY_SSL=false
```

`SQLITE_DB_PATH` is always required. When `API_USAGE=ollama`, `OLLAMA_MODEL`
and `OLLAMA_BASE_URL` are required. When `API_USAGE=NVIDIA`,
`NVIDIA_API_MODEL` and `NVIDIA_API_KEY` are required.
`OLLAMA_TIMEOUT_SECONDS` is optional and defaults to 300 seconds.
`NVIDIA_TIMEOUT_SECONDS` is optional and defaults to `OLLAMA_TIMEOUT_SECONDS`;
increase it for slower hosted models. NVIDIA requests use the certificate
bundle included with `requests` by default. If your network uses a private or
corporate certificate authority and you see `CERTIFICATE_VERIFY_FAILED`, set
`NVIDIA_CA_BUNDLE` to a PEM file containing that CA chain. For local debugging
only, `NVIDIA_VERIFY_SSL=false` disables certificate verification.

Start the dashboard:

```bash
poetry run stratos-quant-ui
```

Open the local URL printed by Gradio. It starts at
`http://127.0.0.1:7860` and automatically uses another available port when
`7860` is occupied.

To request a specific host or port:

```bash
GRADIO_SERVER_PORT=7875 poetry run stratos-quant-ui
GRADIO_SERVER_NAME=0.0.0.0 poetry run stratos-quant-ui
```

The dashboard provides:

1. An active portfolio dropdown populated from SQLite.
2. Hierarchical or Ensemble engine selection.
3. A button that runs allocation, Ollama analysis, screening, and reconciliation.
4. Current allocation and target/drift tables.
5. A scrollable Ollama strategy rationale.
6. A recommendation table with trade values and rationale.
7. An editable `Executed` checkbox persisted to
   `asset_recommendations.is_executed`.

An analysis run writes to the configured database and calls the configured local
Ollama endpoint or NVIDIA API. It does not submit an order to any external
service.

## Run Batch Strategy Reports

Run both allocation models for all active portfolios and write Markdown reports
to the `strategies/` subfolder:

```bash
poetry run stratos-quant-strategies
```

Filter to live or test accounts:

```bash
poetry run stratos-quant-strategies --account-mode live
poetry run stratos-quant-strategies --account-mode test
```

Filter to one or more exact portfolio names, and optionally one model:

```bash
poetry run stratos-quant-strategies \
  --portfolio-name "Core ISA" \
  --portfolio-name "Paper ISA" \
  --model Hierarchical
```

Each report is written as:

```text
strategies/YYYYMMDD_HHMMSS_<portfolio name>_<allocation model>.md
```

The batch command uses the same workflow as the dashboard, so strategy runs,
target allocations, allocation signals, and recommendations are persisted in
SQLite with timestamps before the human-readable Markdown report is written.

## Database expectations

The source database must contain the portfolio and market-data tables used by
the application:

- `accounts`
- `portfolios`
- `transactions`
- `securities`
- `asset_classes`
- `price_history`
- `fx_rate_history`
- `yahoo_fund_profiles`
- `yahoo_fund_metrics`
- `yahoo_fund_performance`

Stratos Quant creates these strategy tables if they are absent:

- `strategy_runs`
- `strategy_target_allocations`
- `strategy_allocation_signals`
- `asset_recommendations`

### Transaction conventions

Holdings are reconstructed as:

```text
BUY quantity - SELL quantity
```

Cash is reconstructed from:

- Deposits.
- Withdrawals.
- Buy and sell values.
- Fees.
- The transaction-level currency exchange rate.

Accurate valuation therefore depends on a complete ledger. Missing deposits,
transfers, or opening balances can produce negative or misleading cash values.

## Quantitative engines

### Hierarchical

The hierarchical engine:

1. Requires sufficient history for its configured windows.
2. Applies a 50/200-day moving-average trend filter.
3. Computes 12-month momentum using PyBroker's vectorized return calculation.
4. Selects the strongest class with positive trend and absolute momentum.
5. Sizes the winner using annualized volatility.
6. Assigns unused risk capacity to `CASH`.

If no class passes the trend and momentum filters, the target is fully defensive.

### Ensemble

The ensemble evaluates three independent sub-portfolios in parallel:

- Moving-average trend allocation.
- Dual-momentum winner selection.
- Inverse-volatility allocation.

Their votes are blended and normalized to exactly `1.0000000000`.

### Asset-class source

The engines, advisory screening, and reconciliation all classify instruments
from `securities.asset_class`. Keep that column populated with the strategy
classes you want the system to allocate and trade.

```sql
SELECT ticker, asset_class
FROM securities
ORDER BY ticker;
```

## Local Ollama advisory

Ollama receives deterministic strategy evidence containing:

- Trend state.
- 12-month momentum.
- Annualized volatility.
- Final target weights.

For fund screening it also receives:

- Annual expense ratios.
- Net assets and fund profile data.
- Available metrics and performance history.
- Securities already held by the portfolio.

Structured screening responses are validated before persistence. Unknown
security IDs, mismatched tickers, malformed actions, and target weights that do
not equal the asset-class target are rejected.

The LLM explains and screens deterministic output; it does not calculate the
portfolio allocation itself.

## Reconciliation behavior

Reconciliation compares each current asset-class value with its target:

```text
target value = portfolio total value × target weight
trade delta  = target value - current value
```

Drift below the configured threshold—1% by default—is suppressed. Remaining
deltas are allocated to LLM-selected securities and persisted as positive trade
value magnitudes, with direction represented by `BUY` or `SELL`.

`CASH` acts as the balancing leg and does not create a fake security order.
Repeated reconciliation updates existing unexecuted recommendations instead of
duplicating them.

## Programmatic usage

### Value a portfolio

```python
from stratos_quant.data import PortfolioValuationService

valuation = PortfolioValuationService().value_portfolio(21)
print(valuation.total_value, valuation.currency)
```

By default, missing prices or FX routes raise `MissingMarketDataError`. Use
`strict=False` only when an explicitly incomplete valuation is acceptable.

### Extract fund context

```python
from stratos_quant.data import FundDataExtractor

context = FundDataExtractor().extract_asset_class("ETF")
context_json = FundDataExtractor().extract_asset_class_json("ETF", indent=2)
```

### Generate an allocation

```python
from stratos_quant.strategy import EnsembleAllocationEngine

allocation = EnsembleAllocationEngine().run()
print(allocation.to_dict()["weights"])
```

### Persist an Ollama rationale

```python
from stratos_quant.db import create_sqlite_engine
from stratos_quant.llm import AdvisoryPipeline, OllamaClient, StrategyRepository

engine = create_sqlite_engine()
pipeline = AdvisoryPipeline(
    OllamaClient(),
    StrategyRepository(engine),
)

run_id = pipeline.rationalize_allocation(
    portfolio_id=21,
    allocation=allocation,
)
```

### Screen funds and reconcile

```python
from decimal import Decimal

from stratos_quant.data import FundDataExtractor, PortfolioValuationService
from stratos_quant.reconciliation import ReconciliationService

target_weight = allocation.weights["EQUITY"]

pipeline.screen_portfolio_asset_class(
    run_id=run_id,
    portfolio_id=21,
    asset_class_code="EQUITY",
    target_weight=target_weight,
    fund_data=FundDataExtractor(engine),
    portfolio_data=PortfolioValuationService(engine),
)

result = ReconciliationService(engine).reconcile(
    run_id=run_id,
    portfolio_id=21,
    drift_threshold=Decimal("0.01"),
)
```

Strategy allocation, fund extraction, and reconciliation use
`securities.asset_class` as the asset-class source of truth.

## Tests

Run the complete suite:

```bash
poetry run pytest -q
```

Additional project checks:

```bash
poetry check --lock
poetry run python -m compileall -q src tests
```

The tests use isolated SQLite fixtures and mocked Ollama responses. They cover
configuration, valuation, FX conversion, fund extraction, both allocation
engines, Ollama validation, persistence, reconciliation, dashboard
orchestration, and execution-status updates.

## Project structure

```text
src/stratos_quant/
├── config/          Environment loading and validation
├── db/              SQLAlchemy connection and strategy schema setup
├── data/            Portfolio valuation and Yahoo data extraction
├── strategy/        Hierarchical and Ensemble allocation engines
├── llm/             Ollama client, prompts, validation, and persistence
├── reconciliation/  Drift calculation and trade mandate generation
└── ui/              Gradio dashboard and orchestration controller
```

## Important limitations

- No broker integration or automatic trade execution is implemented.
- Recommendations are model-assisted analysis, not financial advice.
- Portfolio cash is only as accurate as the transaction ledger.
- A held security without price history cannot be valued in strict mode.
- FX conversion requires a direct, inverse, or connected cross-currency route.
- Securities without enough observations are excluded from strategy signals.
- Yahoo metrics may be absent; missing values are preserved rather than invented.
- The dashboard currently uses the database asset classes unless a controller is
  constructed with a custom mapping.
