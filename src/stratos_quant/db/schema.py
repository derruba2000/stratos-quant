from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


EPIC4_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS strategy_runs (
        id INTEGER NOT NULL PRIMARY KEY,
        run_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        allocation_model VARCHAR(32) NOT NULL,
        llm_model_used VARCHAR(64) NOT NULL,
        llm_overall_rationale TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_target_allocations (
        id INTEGER NOT NULL PRIMARY KEY,
        run_id INTEGER NOT NULL,
        portfolio_id INTEGER NOT NULL,
        asset_class_code VARCHAR(32) NOT NULL,
        target_weight DECIMAL(32, 10) NOT NULL,
        FOREIGN KEY(run_id) REFERENCES strategy_runs (id),
        FOREIGN KEY(portfolio_id) REFERENCES portfolios (id),
        FOREIGN KEY(asset_class_code) REFERENCES asset_classes (code),
        UNIQUE(run_id, portfolio_id, asset_class_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_allocation_signals (
        id INTEGER NOT NULL PRIMARY KEY,
        run_id INTEGER NOT NULL,
        signal_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        signal_scope VARCHAR(16) NOT NULL,
        asset_class_code VARCHAR(32) NOT NULL,
        security_id INTEGER,
        ticker TEXT,
        trend_positive BOOLEAN NOT NULL,
        momentum_12m REAL,
        annualized_volatility REAL,
        security_count INTEGER NOT NULL,
        FOREIGN KEY(run_id) REFERENCES strategy_runs (id),
        FOREIGN KEY(asset_class_code) REFERENCES asset_classes (code),
        FOREIGN KEY(security_id) REFERENCES securities (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_recommendations (
        id INTEGER NOT NULL PRIMARY KEY,
        run_id INTEGER NOT NULL,
        portfolio_id INTEGER NOT NULL,
        security_id INTEGER NOT NULL,
        action_type VARCHAR(10) NOT NULL,
        target_weight DECIMAL(32, 10) NOT NULL,
        estimated_trade_value DECIMAL(32, 10) NOT NULL,
        recommendation_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        llm_security_rationale TEXT NOT NULL,
        is_executed BOOLEAN NOT NULL DEFAULT 0,
        FOREIGN KEY(run_id) REFERENCES strategy_runs (id),
        FOREIGN KEY(portfolio_id) REFERENCES portfolios (id),
        FOREIGN KEY(security_id) REFERENCES securities (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rebalance_runs (
        id INTEGER NOT NULL PRIMARY KEY,
        strategy_run_id INTEGER NOT NULL,
        portfolio_id INTEGER NOT NULL,
        run_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        schedule VARCHAR(16) NOT NULL,
        trigger_reasons TEXT NOT NULL,
        portfolio_value DECIMAL(32, 10) NOT NULL,
        portfolio_drift DECIMAL(32, 10) NOT NULL,
        rebalance_required BOOLEAN NOT NULL,
        expected_benefit DECIMAL(32, 10) NOT NULL,
        estimated_fees DECIMAL(32, 10) NOT NULL,
        estimated_slippage DECIMAL(32, 10) NOT NULL,
        estimated_tax_cost DECIMAL(32, 10) NOT NULL,
        net_expected_benefit DECIMAL(32, 10) NOT NULL,
        explanation TEXT NOT NULL,
        FOREIGN KEY(strategy_run_id) REFERENCES strategy_runs (id),
        FOREIGN KEY(portfolio_id) REFERENCES portfolios (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rebalance_trade_proposals (
        id INTEGER NOT NULL PRIMARY KEY,
        rebalance_run_id INTEGER NOT NULL,
        strategy_run_id INTEGER NOT NULL,
        portfolio_id INTEGER NOT NULL,
        security_id INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        asset_class_code VARCHAR(32) NOT NULL,
        side VARCHAR(10) NOT NULL,
        current_weight DECIMAL(32, 10),
        target_weight DECIMAL(32, 10) NOT NULL,
        allowed_min DECIMAL(32, 10),
        allowed_max DECIMAL(32, 10),
        rebalance_weight DECIMAL(32, 10),
        current_value DECIMAL(32, 10),
        target_value DECIMAL(32, 10),
        trade_value DECIMAL(32, 10) NOT NULL,
        estimated_quantity DECIMAL(32, 10),
        estimated_fees DECIMAL(32, 10) NOT NULL,
        estimated_slippage DECIMAL(32, 10) NOT NULL,
        estimated_tax_cost DECIMAL(32, 10) NOT NULL,
        expected_benefit DECIMAL(32, 10) NOT NULL,
        net_expected_benefit DECIMAL(32, 10) NOT NULL,
        skipped_reason TEXT,
        rationale TEXT NOT NULL,
        FOREIGN KEY(rebalance_run_id) REFERENCES rebalance_runs (id),
        FOREIGN KEY(strategy_run_id) REFERENCES strategy_runs (id),
        FOREIGN KEY(portfolio_id) REFERENCES portfolios (id),
        FOREIGN KEY(security_id) REFERENCES securities (id)
    )
    """,
)

SCHEMA_UPGRADES = {
    "asset_recommendations": {
        "recommendation_timestamp": (
            "ALTER TABLE asset_recommendations "
            "ADD COLUMN recommendation_timestamp DATETIME"
        ),
    },
}


def ensure_strategy_schema(engine: Engine) -> None:
    """Create the Epic 4 strategy persistence tables when absent."""
    with engine.begin() as connection:
        for statement in EPIC4_SCHEMA:
            connection.execute(text(statement))
        for table_name, column_upgrades in SCHEMA_UPGRADES.items():
            existing_columns = {
                str(row._mapping["name"])
                for row in connection.execute(text(f"PRAGMA table_info({table_name})"))
            }
            for column_name, statement in column_upgrades.items():
                if column_name not in existing_columns:
                    connection.execute(text(statement))
        connection.execute(
            text(
                """
                UPDATE asset_recommendations
                SET recommendation_timestamp = CURRENT_TIMESTAMP
                WHERE recommendation_timestamp IS NULL
                """
            )
        )
