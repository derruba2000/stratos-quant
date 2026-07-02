from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Mapping

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from stratos_quant.config import AppConfig, load_settings
from stratos_quant.data import FundDataExtractor, PortfolioValuationService
from stratos_quant.llm import AdvisoryPipeline, StrategyRepository, create_chat_client
from stratos_quant.reconciliation import ReconciliationService
from stratos_quant.strategy import (
    AllocationResult,
    EnsembleAllocationEngine,
    HierarchicalAllocationEngine,
    PriceHistoryLoader,
)


ALLOCATION_COLUMNS = ["Asset Class", "Value", "Weight"]
TARGET_COLUMNS = ["Asset Class", "Target Weight", "Drift", "Suppressed"]
STRATEGY_KPI_COLUMNS = [
    "Ticker",
    "Asset Class",
    "Trend Positive",
    "12M Momentum",
    "24M Momentum",
    "36M Momentum",
    "Annualized Volatility",
    "Sharpe Ratio",
    "Max Drawdown",
    "Beta vs Bench",
    "Alpha",
    "MACD",
    "TWR",
    "CAGR",
    "Sortino Ratio",
    "Treynor Ratio",
    "Yield",
    "Security Count",
]
COMPONENT_COLUMNS = ["Component", "Asset Class", "Weight"]
TRADE_COLUMNS = [
    "Recommendation ID",
    "Action",
    "Ticker",
    "Asset Class",
    "Trade Value",
    "Target Weight",
    "Rationale",
    "Executed",
]
DISPLAY_PRECISION = 2
ORDERS_READY_MESSAGE = (
    "Run an analysis to generate rebalancing orders. If no orders are shown, "
    "the reason will appear here."
)


def _display_number(value: float | Decimal | int | None) -> float | None:
    """Round numeric dashboard values without changing strategy math."""
    if value is None:
        return None
    return round(float(value), DISPLAY_PRECISION)


def _strategy_kpi_row(
    *,
    ticker: str,
    asset_class: str,
    trend_positive: bool,
    signal,
    security_count: int,
) -> dict[str, object]:
    return {
        "Ticker": ticker,
        "Asset Class": asset_class,
        "Trend Positive": trend_positive,
        "12M Momentum": _display_number(signal.momentum_12m),
        "24M Momentum": _display_number(signal.momentum_24m),
        "36M Momentum": _display_number(signal.momentum_36m),
        "Annualized Volatility": _display_number(signal.annualized_volatility),
        "Sharpe Ratio": _display_number(signal.sharpe_ratio),
        "Max Drawdown": _display_number(signal.max_drawdown),
        "Beta vs Bench": _display_number(signal.beta_vs_benchmark),
        "Alpha": _display_number(signal.alpha),
        "MACD": _display_number(signal.macd),
        "TWR": _display_number(signal.time_weighted_return),
        "CAGR": _display_number(signal.cagr),
        "Sortino Ratio": _display_number(signal.sortino_ratio),
        "Treynor Ratio": _display_number(signal.treynor_ratio),
        "Yield": _display_number(signal.yield_),
        "Security Count": security_count,
    }


def _portfolio_kpi_row(rows: list[dict[str, object]]) -> dict[str, object]:
    numeric_columns = [
        column
        for column in STRATEGY_KPI_COLUMNS
        if column not in {"Ticker", "Asset Class", "Trend Positive", "Security Count"}
    ]
    row: dict[str, object] = {
        "Ticker": "Portfolio",
        "Asset Class": "Portfolio",
        "Trend Positive": all(bool(item["Trend Positive"]) for item in rows),
        "Security Count": sum(int(item["Security Count"]) for item in rows),
    }
    for column in numeric_columns:
        values = [
            float(item[column])
            for item in rows
            if item.get(column) is not None
        ]
        row[column] = round(sum(values) / len(values), DISPLAY_PRECISION) if values else None
    return row


class DashboardController:
    """Coordinate the complete analysis pipeline for the Gradio dashboard."""

    def __init__(
        self,
        engine: Engine,
        *,
        settings: AppConfig | None = None,
        asset_class_map: Mapping[str, str] | None = None,
        drift_threshold: Decimal = Decimal("0.01"),
    ) -> None:
        self.engine = engine
        self.settings = settings or load_settings()
        self.asset_class_map = dict(asset_class_map or {})
        self.drift_threshold = drift_threshold

        self.valuation = PortfolioValuationService(engine)
        loader = PriceHistoryLoader(engine)
        self.engines = {
            "Hierarchical": HierarchicalAllocationEngine(loader),
            "Ensemble": EnsembleAllocationEngine(loader),
        }
        self.repository = StrategyRepository(engine)
        self.pipeline = AdvisoryPipeline(
            create_chat_client(self.settings),
            self.repository,
        )
        self.fund_data = FundDataExtractor(
            engine,
            asset_class_map=self.asset_class_map,
        )
        self.reconciliation = ReconciliationService(
            engine,
            valuation_service=self.valuation,
        )

    def portfolio_choices(self) -> list[tuple[str, int]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT p.id, p.name, a.name AS account_name, a.currency_code
                    FROM portfolios p
                    JOIN accounts a ON a.id = p.account_id
                    WHERE p.is_active = 1 AND a.is_active = 1
                    ORDER BY a.name, p.name, p.id
                    """
                )
            ).mappings().all()
        return [
            (
                f"{row['account_name']} · {row['name']} "
                f"({row['currency_code']}) [#{row['id']}]",
                int(row["id"]),
            )
            for row in rows
        ]

    def _llm_provider_label(self) -> str:
        settings = getattr(self, "settings", None)
        return getattr(settings, "llm_provider_label", "Ollama")

    def run_analysis(
        self,
        portfolio_id: int | str,
        model_name: str,
        *,
        precomputed_allocation: AllocationResult | None = None,
    ) -> tuple[
        pd.DataFrame,
        pd.DataFrame,
        pd.DataFrame,
        pd.DataFrame,
        str,
        str,
        pd.DataFrame,
        int | None,
        str,
    ]:
        """Run optimization, LLM screening, reconciliation, and persistence.

        When *precomputed_allocation* is provided (e.g. from a batch runner that
        already computed signals once for all portfolios), the price-history scan
        inside the allocation engine is skipped.
        """
        try:
            resolved_portfolio_id = int(portfolio_id)
            strategy_engine = self.engines[model_name]
            valuation = self.valuation.value_portfolio(resolved_portfolio_id)
            allocation = (
                precomputed_allocation
                if precomputed_allocation is not None
                else strategy_engine.run(asset_class_map=self.asset_class_map)
            )
            current_frame = self._current_allocation_frame(valuation)
            target_frame = self._allocation_target_frame(valuation, allocation)
            kpi_frame = self._strategy_kpi_frame(allocation)
            component_frame = self._component_frame(allocation)
            ledger_warning = self._ledger_warning(valuation)
        except Exception as exc:
            return (
                pd.DataFrame(columns=ALLOCATION_COLUMNS),
                pd.DataFrame(columns=TARGET_COLUMNS),
                pd.DataFrame(columns=STRATEGY_KPI_COLUMNS),
                pd.DataFrame(columns=COMPONENT_COLUMNS),
                f"### Analysis failed\n\n`{type(exc).__name__}: {exc}`",
                (
                    "No orders generated because the allocation analysis failed. "
                    "Review the failure message above first."
                ),
                pd.DataFrame(columns=TRADE_COLUMNS),
                None,
                "Allocation analysis failed. Review the message below.",
            )

        run_id: int | None = None
        try:
            run_id = self.pipeline.rationalize_allocation(
                portfolio_id=resolved_portfolio_id,
                allocation=allocation,
            )
            if ledger_warning is not None:
                run = self.repository.get_run(run_id)
                return (
                    current_frame,
                    target_frame,
                    kpi_frame,
                    component_frame,
                    (
                        f"### Ledger warning\n\n{ledger_warning}\n\n"
                        f"### {self._llm_provider_label()} strategy rationale\n\n"
                        f"{run['llm_overall_rationale']}"
                    ),
                    (
                        "No orders generated. Reconciliation is disabled because "
                        "ledger quality is blocking trade generation. The "
                        "reconstructed cash balance is negative, "
                        "indicating missing **initial funding transactions**. "
                        "To enable rebalancing: Add a DEPOSIT transaction that records "
                        "the original account funding (this does not re-count your holdings, "
                        "which are already recorded). Alternatively, if you used margin "
                        "borrowing, model that separately."
                    ),
                    pd.DataFrame(columns=TRADE_COLUMNS),
                    run_id,
                    (
                        "Allocation completed; no orders generated because "
                        "reconciliation is blocked by ledger quality."
                    ),
                )
            for asset_class_code, target_weight in allocation.weights.items():
                if asset_class_code == "CASH" or target_weight <= 0:
                    continue
                self.pipeline.screen_portfolio_asset_class(
                    run_id=run_id,
                    portfolio_id=resolved_portfolio_id,
                    asset_class_code=asset_class_code,
                    target_weight=target_weight,
                    fund_data=self.fund_data,
                    portfolio_data=self.valuation,
                )
            reconciliation = self.reconciliation.reconcile(
                run_id=run_id,
                portfolio_id=resolved_portfolio_id,
                drift_threshold=self.drift_threshold,
                asset_class_map=self.asset_class_map,
            )
            run = self.repository.get_run(run_id)
            trade_frame = self._trade_frame(run_id, resolved_portfolio_id)

            return (
                current_frame,
                self._target_frame(reconciliation),
                kpi_frame,
                component_frame,
                str(run["llm_overall_rationale"]),
                self._orders_message(trade_frame),
                trade_frame,
                run_id,
                f"Run #{run_id} completed with {model_name}.",
            )
        except Exception as exc:
            trades = pd.DataFrame(columns=TRADE_COLUMNS)
            if run_id is not None:
                try:
                    trades = self._trade_frame(run_id, resolved_portfolio_id)
                except Exception:
                    pass
            return (
                current_frame,
                target_frame,
                kpi_frame,
                component_frame,
                (
                    (
                        f"### Ledger warning\n\n{ledger_warning}\n\n"
                        if ledger_warning is not None
                        else ""
                    )
                    + "### Allocation completed; advisory pipeline failed\n\n"
                    f"`{type(exc).__name__}: {exc}`"
                ),
                (
                    "No complete order set was generated because the advisory or "
                    "reconciliation stage failed. Allocation tables remain visible "
                    "above so you can still inspect the deterministic strategy output."
                    if trades.empty
                    else (
                        "Partial order data is shown from the current run, but the "
                        "advisory or reconciliation stage reported a failure."
                    )
                ),
                trades,
                run_id,
                "Allocation is shown, but advisory/trade generation failed.",
            )

    def update_executed(
        self,
        trades: pd.DataFrame | list[list[object]] | None,
    ) -> tuple[pd.DataFrame, str]:
        """Persist checkbox edits from the recommendation table."""
        frame = self._coerce_trade_frame(trades)
        if frame.empty:
            return frame, "No generated trades to update."
        for row in frame.to_dict(orient="records"):
            self.repository.set_recommendation_executed(
                int(row["Recommendation ID"]),
                bool(row["Executed"]),
            )
        return frame, "Execution status saved."

    def _current_allocation_frame(self, valuation) -> pd.DataFrame:
        values = self._current_values(valuation)
        positive_total = sum(
            (value for value in values.values() if value > 0),
            Decimal("0"),
        )
        return pd.DataFrame(
            [
                {
                    "Asset Class": code,
                    # Market value cannot be negative; negative cash is a ledger
                    # artefact (missing deposits).  Floor at 0 for display so the
                    # weight maths and the visible figure stay consistent.
                    "Value": _display_number(max(value, Decimal("0"))),
                    "Weight": (
                        _display_number(value / positive_total)
                        if value > 0 and positive_total > 0
                        else 0.0
                    ),
                }
                for code, value in sorted(values.items())
            ],
            columns=ALLOCATION_COLUMNS,
        )

    def _current_values(self, valuation) -> dict[str, Decimal]:
        values: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for holding in valuation.holdings:
            code = self.asset_class_map.get(
                holding.ticker.upper(),
                holding.asset_class_code,
            ).upper()
            values[code] += holding.market_value or Decimal("0")
        values["CASH"] += valuation.cash_balance
        return dict(values)

    def _allocation_target_frame(self, valuation, allocation) -> pd.DataFrame:
        current_values = self._current_values(valuation)
        positive_total = sum(
            (value for value in current_values.values() if value > 0),
            Decimal("0"),
        )
        rows = []
        for code in sorted(set(current_values) | set(allocation.weights)):
            target_weight = allocation.weights.get(code, Decimal("0"))
            current_value = current_values.get(code, Decimal("0"))
            current_weight = (
                current_value / positive_total
                if current_value > 0 and positive_total > 0
                else Decimal("0")
            )
            drift = target_weight - current_weight
            rows.append(
                {
                    "Asset Class": code,
                    "Target Weight": _display_number(target_weight),
                    "Drift": _display_number(drift),
                    "Suppressed": abs(drift) < self.drift_threshold,
                }
            )
        return pd.DataFrame(
            rows,
            columns=TARGET_COLUMNS,
        )

    @staticmethod
    def _strategy_kpi_frame(allocation) -> pd.DataFrame:
        if allocation.security_signals:
            rows = [
                _strategy_kpi_row(
                    ticker=signal.ticker,
                    asset_class=signal.asset_class_code,
                    trend_positive=signal.trend_positive,
                    signal=signal,
                    security_count=1,
                )
                for signal in allocation.security_signals
            ]
        else:
            rows = [
                _strategy_kpi_row(
                    ticker="Class aggregate",
                    asset_class=signal.asset_class_code,
                    trend_positive=signal.trend_positive,
                    signal=signal,
                    security_count=signal.security_count,
                )
                for signal in allocation.signals
            ]
        if rows:
            rows.insert(0, _portfolio_kpi_row(rows))
        return pd.DataFrame(rows, columns=STRATEGY_KPI_COLUMNS)

    @staticmethod
    def _component_frame(allocation) -> pd.DataFrame:
        rows = []
        for component, weights in (allocation.component_weights or {}).items():
            for asset_class_code, weight in weights.items():
                rows.append(
                    {
                        "Component": component,
                        "Asset Class": asset_class_code,
                        "Weight": _display_number(weight),
                    }
                )
        return pd.DataFrame(rows, columns=COMPONENT_COLUMNS)

    @staticmethod
    def _ledger_warning(valuation) -> str | None:
        if valuation.cash_balance >= 0:
            return None
        return (
            f"**Ledger quality issue:** Reconstructed cash is **{valuation.cash_balance:,.2f} "
            f"{valuation.currency}** (negative). The transaction ledger lacks the initial "
            "funding transactions that opened this account. Your holdings are already recorded; "
            "you just need to add the **DEPOSIT transactions** that show where the original "
            "capital came from. Alternatively, if borrowing was used, model that separately. "
            "Allocation weights below are normalized across positive asset values only. "
            "Trade reconciliation is disabled until the ledger is corrected."
        )

    @staticmethod
    def _target_frame(reconciliation) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "Asset Class": drift.asset_class_code,
                    "Target Weight": _display_number(drift.target_weight),
                    "Drift": _display_number(drift.drift_weight),
                    "Suppressed": drift.suppressed,
                }
                for drift in reconciliation.drifts
            ],
            columns=TARGET_COLUMNS,
        )

    def _trade_frame(self, run_id: int, portfolio_id: int) -> pd.DataFrame:
        rows = self.repository.get_recommendations(
            run_id=run_id,
            portfolio_id=portfolio_id,
        )
        return pd.DataFrame(
            [
                {
                    "Recommendation ID": int(row["id"]),
                    "Action": str(row["action_type"]),
                    "Ticker": str(row["ticker"]),
                    "Asset Class": str(row["asset_class"]),
                    "Trade Value": _display_number(row["estimated_trade_value"]),
                    "Target Weight": _display_number(row["target_weight"]),
                    "Rationale": str(row["llm_security_rationale"]),
                    "Executed": bool(row["is_executed"]),
                }
                for row in rows
            ],
            columns=TRADE_COLUMNS,
        )

    @staticmethod
    def _orders_message(trades: pd.DataFrame) -> str:
        if trades.empty:
            return (
                "No orders generated. The portfolio is already within the drift "
                "threshold, or the advisory screen did not produce an eligible "
                "security recommendation for the active target classes."
            )
        return f"{len(trades)} actionable order(s) generated. Tick Executed only after placing the trade externally."

    @staticmethod
    def _coerce_trade_frame(
        trades: pd.DataFrame | list[list[object]] | None,
    ) -> pd.DataFrame:
        if trades is None:
            return pd.DataFrame(columns=TRADE_COLUMNS)
        if isinstance(trades, pd.DataFrame):
            return trades
        return pd.DataFrame(trades, columns=TRADE_COLUMNS)
