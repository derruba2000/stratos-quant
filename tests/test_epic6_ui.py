from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
from sqlalchemy import create_engine, text

from stratos_quant.config import AppConfig
from stratos_quant.data import HoldingValuation, PortfolioValuation
from stratos_quant.llm import StrategyRepository
from stratos_quant.reconciliation import AssetClassDrift, ReconciliationResult
from stratos_quant.strategy import AllocationResult, AssetClassSignal
from stratos_quant.ui import DashboardController, build_app
from stratos_quant.ui.app import launch_network_options
from stratos_quant.ui.controller import TRADE_COLUMNS


class FakeValuationService:
    def value_portfolio(self, portfolio_id):
        return PortfolioValuation(
            portfolio_id=int(portfolio_id),
            portfolio_name="Demo",
            currency="GBP",
            cash_balance=Decimal("200"),
            holdings_value=Decimal("800"),
            total_value=Decimal("1000"),
            holdings=(
                HoldingValuation(
                    security_id=10,
                    ticker="DEMO",
                    name="Demo Fund",
                    asset_class_code="ETF",
                    security_currency="GBP",
                    quantity=Decimal("8"),
                    price_date=date(2026, 6, 25),
                    latest_close=Decimal("100"),
                    fx_rate=Decimal("1"),
                    market_value=Decimal("800"),
                ),
            ),
        )


class FakeStrategyEngine:
    def run(self, *, asset_class_map):
        return AllocationResult(
            model="HIERARCHICAL",
            as_of=date(2026, 6, 25),
            weights={"ETF": Decimal("1.0000000000")},
            signals=(
                AssetClassSignal(
                    asset_class_code="ETF",
                    trend_positive=True,
                    momentum_12m=0.2,
                    annualized_volatility=0.1,
                    security_count=1,
                ),
            ),
        )


class FakePipeline:
    def __init__(self):
        self.screened = []

    def rationalize_allocation(self, *, portfolio_id, allocation):
        return 42

    def screen_portfolio_asset_class(self, **kwargs):
        self.screened.append(kwargs)


class FailingPipeline(FakePipeline):
    def rationalize_allocation(self, *, portfolio_id, allocation):
        raise RuntimeError("Ollama unavailable")


class FakeReconciliation:
    def reconcile(self, **kwargs):
        return ReconciliationResult(
            run_id=42,
            portfolio_id=7,
            currency="GBP",
            portfolio_value=Decimal("1000"),
            drift_threshold=Decimal("0.01"),
            drifts=(
                AssetClassDrift(
                    asset_class_code="ETF",
                    current_value=Decimal("800"),
                    current_weight=Decimal("0.8"),
                    target_value=Decimal("1000"),
                    target_weight=Decimal("1"),
                    drift_value=Decimal("200"),
                    drift_weight=Decimal("0.2"),
                    suppressed=False,
                ),
                AssetClassDrift(
                    asset_class_code="CASH",
                    current_value=Decimal("200"),
                    current_weight=Decimal("0.2"),
                    target_value=Decimal("0"),
                    target_weight=Decimal("0"),
                    drift_value=Decimal("-200"),
                    drift_weight=Decimal("-0.2"),
                    suppressed=False,
                ),
            ),
            mandates=(),
        )


class FakeRepository:
    def __init__(self):
        self.execution_updates = []

    def get_run(self, run_id):
        return {"llm_overall_rationale": "The trend is positive."}

    def get_recommendations(self, *, run_id, portfolio_id):
        return [
            {
                "id": 5,
                "action_type": "BUY",
                "target_weight": Decimal("1"),
                "estimated_trade_value": Decimal("200"),
                "llm_security_rationale": "Low fee candidate.",
                "is_executed": False,
                "ticker": "DEMO",
                "name": "Demo Fund",
                "asset_class": "ETF",
            }
        ]

    def set_recommendation_executed(self, recommendation_id, is_executed):
        self.execution_updates.append((recommendation_id, is_executed))


def _fake_controller():
    controller = object.__new__(DashboardController)
    controller.asset_class_map = {}
    controller.drift_threshold = Decimal("0.01")
    controller.valuation = FakeValuationService()
    controller.engines = {
        "Hierarchical": FakeStrategyEngine(),
        "Ensemble": FakeStrategyEngine(),
    }
    controller.pipeline = FakePipeline()
    controller.repository = FakeRepository()
    controller.fund_data = object()
    controller.reconciliation = FakeReconciliation()
    return controller


def test_dashboard_controller_runs_full_pipeline_and_formats_outputs():
    controller = _fake_controller()

    (
        current,
        target,
        kpis,
        components,
        rationale,
        orders_note,
        trades,
        run_id,
        status,
    ) = controller.run_analysis(7, "Hierarchical")

    assert run_id == 42
    assert status == "Run #42 completed with Hierarchical."
    assert rationale == "The trend is positive."
    assert orders_note == (
        "1 actionable order(s) generated. Tick Executed only after placing the "
        "trade externally."
    )
    assert set(current["Asset Class"]) == {"ETF", "CASH"}
    assert set(target["Asset Class"]) == {"ETF", "CASH"}
    assert kpis.loc[0, "12M Momentum"] == 0.2
    assert components.empty
    assert trades.loc[0, "Action"] == "BUY"
    assert trades.loc[0, "Trade Value"] == 200.0
    assert controller.pipeline.screened[0]["asset_class_code"] == "ETF"


def test_dashboard_execution_checkbox_updates_database_state():
    controller = _fake_controller()
    frame = pd.DataFrame(
        [[5, "BUY", "DEMO", "ETF", 200.0, 1.0, "Reason", True]],
        columns=TRADE_COLUMNS,
    )

    returned, status = controller.update_executed(frame)

    assert returned.equals(frame)
    assert status == "Execution status saved."
    assert controller.repository.execution_updates == [(5, True)]


def test_dashboard_keeps_allocation_visible_when_advisory_fails():
    controller = _fake_controller()
    controller.pipeline = FailingPipeline()

    (
        current,
        target,
        kpis,
        components,
        rationale,
        orders_note,
        trades,
        run_id,
        status,
    ) = controller.run_analysis(7, "Hierarchical")

    assert set(current["Asset Class"]) == {"ETF", "CASH"}
    assert set(target["Asset Class"]) == {"ETF", "CASH"}
    assert target.loc[target["Asset Class"] == "ETF", "Target Weight"].item() == 1.0
    assert not kpis.empty
    assert components.empty
    assert "Ollama unavailable" in rationale
    assert "No complete order set was generated" in orders_note
    assert trades.empty
    assert run_id is None
    assert status == "Allocation is shown, but advisory/trade generation failed."


def test_negative_cash_uses_positive_asset_weights_and_blocks_reconciliation():
    controller = _fake_controller()
    controller.valuation = FakeValuationService()
    controller.valuation.value_portfolio = lambda portfolio_id: PortfolioValuation(
        portfolio_id=7,
        portfolio_name="Incomplete Ledger",
        currency="EUR",
        cash_balance=Decimal("-600"),
        holdings_value=Decimal("1000"),
        total_value=Decimal("400"),
        holdings=(
            HoldingValuation(
                security_id=10,
                ticker="DEMO",
                name="Demo Fund",
                asset_class_code="ETF",
                security_currency="EUR",
                quantity=Decimal("10"),
                price_date=date(2026, 6, 25),
                latest_close=Decimal("100"),
                fx_rate=Decimal("1"),
                market_value=Decimal("1000"),
            ),
        ),
    )

    (
        current,
        target,
        _kpis,
        _components,
        rationale,
        orders_note,
        trades,
        run_id,
        status,
    ) = controller.run_analysis(7, "Hierarchical")

    weights = dict(zip(current["Asset Class"], current["Weight"], strict=True))
    assert weights == {"CASH": 0.0, "ETF": 1.0}
    drifts = dict(zip(target["Asset Class"], target["Drift"], strict=True))
    assert drifts == {"CASH": 0.0, "ETF": 0.0}
    assert "Reconstructed cash is **-600.00 EUR**" in rationale
    assert "No orders generated. Reconciliation is disabled" in orders_note
    assert trades.empty
    assert run_id == 42
    assert (
        status
        == (
            "Allocation completed; no orders generated because "
            "reconciliation is blocked by ledger quality."
        )
    )


def test_portfolio_dropdown_choices_come_from_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ui.sqlite3'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY,
                name TEXT,
                currency_code TEXT,
                is_active BOOLEAN
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE portfolios (
                id INTEGER PRIMARY KEY,
                account_id INTEGER,
                name TEXT,
                is_active BOOLEAN
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO accounts VALUES (1, 'Broker', 'GBP', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO portfolios VALUES (7, 1, 'ISA', 1)"
        )

    controller = object.__new__(DashboardController)
    controller.engine = engine

    assert controller.portfolio_choices() == [("Broker · ISA (GBP) [#7]", 7)]


def test_repository_updates_executed_checkbox_state(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'executed.sqlite3'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE portfolios (id INTEGER PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE asset_classes (code VARCHAR(32) PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE securities (id INTEGER PRIMARY KEY)"
        )
        connection.exec_driver_sql("INSERT INTO portfolios VALUES (7)")
        connection.exec_driver_sql("INSERT INTO asset_classes VALUES ('ETF')")
        connection.exec_driver_sql("INSERT INTO securities VALUES (10)")
    repository = StrategyRepository(engine)
    with engine.begin() as connection:
        run_id = connection.execute(
            text(
                """
                INSERT INTO strategy_runs
                    (allocation_model, llm_model_used, llm_overall_rationale)
                VALUES ('HIERARCHICAL', 'gemma4', 'Rationale')
                """
            )
        ).lastrowid
        recommendation_id = connection.execute(
            text(
                """
                INSERT INTO asset_recommendations
                    (run_id, portfolio_id, security_id, action_type,
                     target_weight, estimated_trade_value,
                     llm_security_rationale, is_executed)
                VALUES (:run_id, 7, 10, 'BUY', 1, 100, 'Reason', 0)
                """
            ),
            {"run_id": run_id},
        ).lastrowid

    repository.set_recommendation_executed(recommendation_id, True)

    with engine.connect() as connection:
        executed = connection.execute(
            text(
                "SELECT is_executed FROM asset_recommendations "
                "WHERE id = :recommendation_id"
            ),
            {"recommendation_id": recommendation_id},
        ).scalar_one()
    assert executed == 1


def test_gradio_app_builds_with_epic6_controls(monkeypatch, tmp_path):
    db_file = tmp_path / "ui.sqlite3"
    db_file.touch()
    settings = AppConfig(
        sqlite_db_path=db_file,
        ollama_model="gemma4",
        ollama_base_url="http://localhost:11434",
    )
    fake = _fake_controller()
    fake.portfolio_choices = lambda: [("Demo Portfolio", 7)]
    monkeypatch.setattr("stratos_quant.ui.app.load_settings", lambda: settings)

    app = build_app(fake)
    config = app.get_config_file()
    component_types = {component["type"] for component in config["components"]}

    assert {"dropdown", "radio", "button", "dataframe", "markdown"} <= component_types
    assert any(
        component.get("props", {}).get("value")
        == "Trigger rebalance analysis run"
        for component in config["components"]
    )


def test_launch_uses_automatic_port_selection_by_default(monkeypatch):
    monkeypatch.delenv("GRADIO_SERVER_PORT", raising=False)
    monkeypatch.delenv("GRADIO_SERVER_NAME", raising=False)

    assert launch_network_options() == {
        "server_name": "127.0.0.1",
        "server_port": None,
    }


def test_launch_respects_gradio_network_overrides(monkeypatch):
    monkeypatch.setenv("GRADIO_SERVER_PORT", "7875")
    monkeypatch.setenv("GRADIO_SERVER_NAME", "0.0.0.0")

    assert launch_network_options() == {
        "server_name": "0.0.0.0",
        "server_port": 7875,
    }
