from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from stratos_quant.data import HoldingValuation, PortfolioValuation
from stratos_quant.reconciliation import ReconciliationError, ReconciliationService


class FakeValuationService:
    def __init__(self, valuation):
        self.valuation = valuation
        self.calls = []

    def value_portfolio(self, portfolio_id, *, as_of=None, strict=True):
        self.calls.append(
            {"portfolio_id": portfolio_id, "as_of": as_of, "strict": strict}
        )
        return self.valuation


@pytest.fixture
def reconciliation_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'reconciliation.sqlite3'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE portfolios (id INTEGER PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE asset_classes (code VARCHAR(32) PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE securities (
                id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                asset_class VARCHAR(32) NOT NULL
            )
            """
        )
        connection.exec_driver_sql("INSERT INTO portfolios VALUES (7)")
        connection.exec_driver_sql(
            "INSERT INTO asset_classes VALUES ('ETF'), ('CASH')"
        )
        connection.exec_driver_sql(
            """
            INSERT INTO securities VALUES
                (10, 'SELECTED', 'ETF'),
                (11, 'LEGACY', 'ETF')
            """
        )
    return engine


@pytest.fixture
def portfolio_valuation():
    return PortfolioValuation(
        portfolio_id=7,
        portfolio_name="Portfolio",
        currency="GBP",
        cash_balance=Decimal("4000"),
        holdings_value=Decimal("6000"),
        total_value=Decimal("10000"),
        holdings=(
            HoldingValuation(
                security_id=10,
                ticker="SELECTED",
                name="Selected Fund",
                asset_class_code="ETF",
                security_currency="GBP",
                quantity=Decimal("40"),
                price_date=None,
                latest_close=Decimal("100"),
                fx_rate=Decimal("1"),
                market_value=Decimal("4000"),
            ),
            HoldingValuation(
                security_id=11,
                ticker="LEGACY",
                name="Legacy Fund",
                asset_class_code="ETF",
                security_currency="GBP",
                quantity=Decimal("20"),
                price_date=None,
                latest_close=Decimal("100"),
                fx_rate=Decimal("1"),
                market_value=Decimal("2000"),
            ),
        ),
    )


def _seed_run(engine, *, etf_target: str, cash_target: str) -> int:
    service = ReconciliationService(
        engine,
        valuation_service=FakeValuationService(None),
    )
    del service
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
        connection.execute(
            text(
                """
                INSERT INTO strategy_target_allocations
                    (run_id, portfolio_id, asset_class_code, target_weight)
                VALUES
                    (:run_id, 7, 'ETF', :etf_target),
                    (:run_id, 7, 'CASH', :cash_target)
                """
            ),
            {
                "run_id": run_id,
                "etf_target": Decimal(etf_target),
                "cash_target": Decimal(cash_target),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO asset_recommendations
                    (run_id, portfolio_id, security_id, action_type,
                     target_weight, estimated_trade_value,
                     llm_security_rationale, is_executed)
                VALUES
                    (:run_id, 7, 10, 'BUY', :target_weight, 0,
                     'Selected for lower costs and stronger performance.', 0)
                """
            ),
            {
                "run_id": run_id,
                "target_weight": Decimal(etf_target),
            },
        )
    return int(run_id)


def test_reconciliation_generates_and_persists_buy_and_sell_mandates(
    reconciliation_engine,
    portfolio_valuation,
):
    run_id = _seed_run(
        reconciliation_engine,
        etf_target="0.7",
        cash_target="0.3",
    )
    valuation_service = FakeValuationService(portfolio_valuation)
    service = ReconciliationService(
        reconciliation_engine,
        valuation_service=valuation_service,
    )

    result = service.reconcile(
        run_id=run_id,
        portfolio_id=7,
        drift_threshold=Decimal("0.01"),
    )

    assert result.portfolio_value == Decimal("10000")
    assert valuation_service.calls[0]["strict"] is True
    drifts = {drift.asset_class_code: drift for drift in result.drifts}
    assert drifts["ETF"].current_weight == Decimal("0.6000000000")
    assert drifts["ETF"].target_weight == Decimal("0.7")
    assert drifts["ETF"].drift_value == Decimal("1000.0")
    assert not drifts["ETF"].suppressed
    assert drifts["CASH"].drift_value == Decimal("-1000.0")

    mandates = {mandate.ticker: mandate for mandate in result.mandates}
    assert mandates["SELECTED"].action_type == "BUY"
    assert mandates["SELECTED"].estimated_trade_value == Decimal("3000.00")
    assert mandates["SELECTED"].target_weight == Decimal("0.7")
    assert mandates["LEGACY"].action_type == "SELL"
    assert mandates["LEGACY"].estimated_trade_value == Decimal("2000.00")
    assert mandates["LEGACY"].target_weight == Decimal("0")

    with reconciliation_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT s.ticker, ar.action_type, ar.target_weight,
                       ar.estimated_trade_value, ar.llm_security_rationale
                FROM asset_recommendations ar
                JOIN securities s ON s.id = ar.security_id
                WHERE ar.run_id = :run_id
                ORDER BY s.ticker
                """
            ),
            {"run_id": run_id},
        ).mappings().all()

    assert len(rows) == 2
    assert rows[0]["ticker"] == "LEGACY"
    assert rows[0]["action_type"] == "SELL"
    assert Decimal(str(rows[0]["estimated_trade_value"])) == Decimal("2000")
    assert "not selected" in rows[0]["llm_security_rationale"]
    assert rows[1]["ticker"] == "SELECTED"
    assert Decimal(str(rows[1]["estimated_trade_value"])) == Decimal("3000")
    assert rows[1]["llm_security_rationale"].startswith("Selected for lower")


def test_reconciliation_suppresses_drift_below_threshold(
    reconciliation_engine,
    portfolio_valuation,
):
    run_id = _seed_run(
        reconciliation_engine,
        etf_target="0.605",
        cash_target="0.395",
    )
    service = ReconciliationService(
        reconciliation_engine,
        valuation_service=FakeValuationService(portfolio_valuation),
    )

    result = service.reconcile(
        run_id=run_id,
        portfolio_id=7,
        drift_threshold=Decimal("0.01"),
    )

    assert result.mandates == ()
    assert all(drift.suppressed for drift in result.drifts)
    with reconciliation_engine.connect() as connection:
        value = connection.execute(
            text(
                "SELECT estimated_trade_value FROM asset_recommendations "
                "WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        ).scalar_one()
    assert Decimal(str(value)) == Decimal("0")


def test_reconciliation_is_idempotent_for_generated_sell_rows(
    reconciliation_engine,
    portfolio_valuation,
):
    run_id = _seed_run(
        reconciliation_engine,
        etf_target="0.7",
        cash_target="0.3",
    )
    service = ReconciliationService(
        reconciliation_engine,
        valuation_service=FakeValuationService(portfolio_valuation),
    )

    service.reconcile(run_id=run_id, portfolio_id=7)
    service.reconcile(run_id=run_id, portfolio_id=7)

    with reconciliation_engine.connect() as connection:
        count = connection.execute(
            text(
                "SELECT COUNT(*) FROM asset_recommendations "
                "WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        ).scalar_one()
    assert count == 2


def test_selected_security_remains_a_target_after_action_changes_to_sell(
    reconciliation_engine,
    portfolio_valuation,
):
    overallocated = PortfolioValuation(
        portfolio_id=portfolio_valuation.portfolio_id,
        portfolio_name=portfolio_valuation.portfolio_name,
        currency=portfolio_valuation.currency,
        cash_balance=Decimal("3000"),
        holdings_value=Decimal("7000"),
        total_value=Decimal("10000"),
        holdings=(
            HoldingValuation(
                security_id=10,
                ticker="SELECTED",
                name="Selected Fund",
                asset_class_code="ETF",
                security_currency="GBP",
                quantity=Decimal("70"),
                price_date=None,
                latest_close=Decimal("100"),
                fx_rate=Decimal("1"),
                market_value=Decimal("7000"),
            ),
        ),
    )
    run_id = _seed_run(
        reconciliation_engine,
        etf_target="0.6",
        cash_target="0.4",
    )
    service = ReconciliationService(
        reconciliation_engine,
        valuation_service=FakeValuationService(overallocated),
    )

    first = service.reconcile(run_id=run_id, portfolio_id=7)
    second = service.reconcile(run_id=run_id, portfolio_id=7)

    assert first.mandates[0].action_type == "SELL"
    assert first.mandates[0].estimated_trade_value == Decimal("1000.00")
    assert second.mandates == first.mandates


def test_positive_target_requires_llm_selected_security(
    reconciliation_engine,
    portfolio_valuation,
):
    run_id = _seed_run(
        reconciliation_engine,
        etf_target="0.7",
        cash_target="0.3",
    )
    with reconciliation_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM asset_recommendations WHERE run_id = :run_id"),
            {"run_id": run_id},
        )
    service = ReconciliationService(
        reconciliation_engine,
        valuation_service=FakeValuationService(portfolio_valuation),
    )

    with pytest.raises(ReconciliationError, match="No LLM-selected securities"):
        service.reconcile(run_id=run_id, portfolio_id=7)
