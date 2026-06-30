from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine

from stratos_quant.batch import (
    PortfolioBatchTarget,
    StrategyBatchRunner,
    discover_active_portfolios,
)


class FixedClock:
    @staticmethod
    def now():
        return datetime(2026, 6, 30, 14, 5, 6)


class FakeBatchController:
    def __init__(self):
        self.calls = []

    def run_analysis(self, portfolio_id, model_name):
        self.calls.append((portfolio_id, model_name))
        return (
            pd.DataFrame(
                [{"Asset Class": "ETF", "Value": 1000.0, "Weight": 1.0}]
            ),
            pd.DataFrame(
                [
                    {
                        "Asset Class": "ETF",
                        "Target Weight": 1.0,
                        "Drift": 0.0,
                        "Suppressed": True,
                    }
                ]
            ),
            pd.DataFrame(
                [
                    {
                        "Ticker": "DEMO",
                        "Asset Class": "ETF",
                        "Trend Positive": True,
                        "12M Momentum": 0.2,
                        "Annualized Volatility": 0.1,
                        "Security Count": 1,
                    }
                ]
            ),
            pd.DataFrame(
                [{"Component": "Momentum", "Asset Class": "ETF", "Weight": 1.0}]
            ),
            "The strategy rationale.",
            "1 actionable order(s) generated.",
            pd.DataFrame(
                [
                    {
                        "Recommendation ID": 5,
                        "Action": "BUY",
                        "Ticker": "DEMO",
                        "Asset Class": "ETF",
                        "Trade Value": 200.0,
                        "Target Weight": 1.0,
                        "Rationale": "Low fee candidate.",
                        "Executed": False,
                    }
                ]
            ),
            42,
            f"Run #42 completed with {model_name}.",
        )


def test_discover_active_portfolios_filters_mode_and_names(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'batch.sqlite3'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY,
                name TEXT,
                currency_code TEXT,
                is_simulated BOOLEAN,
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
            """
            INSERT INTO accounts VALUES
                (1, 'Live Broker', 'EUR', 0, 1),
                (2, 'Sandbox', 'GBP', 1, 1),
                (3, 'Inactive', 'USD', 0, 0)
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO portfolios VALUES
                (7, 1, 'Core ISA', 1),
                (8, 2, 'Paper ISA', 1),
                (9, 1, 'Dormant', 0),
                (10, 3, 'Inactive Account', 1)
            """
        )

    live = discover_active_portfolios(engine, account_mode="live")
    test = discover_active_portfolios(
        engine,
        account_mode="test",
        portfolio_names=["paper isa"],
    )

    assert [(row.portfolio_id, row.portfolio_name) for row in live] == [
        (7, "Core ISA")
    ]
    assert [(row.portfolio_id, row.portfolio_name, row.is_simulated) for row in test] == [
        (8, "Paper ISA", True)
    ]


def test_batch_runner_writes_markdown_report_for_each_model(tmp_path):
    controller = FakeBatchController()
    runner = StrategyBatchRunner(
        controller,
        output_dir=tmp_path / "strategies",
        clock=FixedClock,
    )
    portfolio = PortfolioBatchTarget(
        portfolio_id=7,
        portfolio_name="Core ISA",
        account_name="Live Broker",
        currency_code="EUR",
        is_simulated=False,
    )

    reports = runner.run([portfolio], models=("Hierarchical", "Ensemble"))

    assert controller.calls == [(7, "Hierarchical"), (7, "Ensemble")]
    assert [report.path.name for report in reports] == [
        "20260630_140506_Core_ISA_Hierarchical.md",
        "20260630_140506_Core_ISA_Ensemble.md",
    ]
    content = reports[0].path.read_text(encoding="utf-8")
    assert "# Core ISA - Hierarchical" in content
    assert "- Run ID: `42`" in content
    assert "## Current Assets" in content
    assert "## Target Allocation And Drift" in content
    assert "## Strategy KPI Table" in content
    assert "## Ensemble Component Weights" in content
    assert "## LLM Strategy Rationale" in content
    assert "The strategy rationale." in content
    assert "## Generated Recommendations" in content
    assert "Low fee candidate." in content
