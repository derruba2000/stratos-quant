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
        llm_security_rationale TEXT NOT NULL,
        is_executed BOOLEAN NOT NULL DEFAULT 0,
        FOREIGN KEY(run_id) REFERENCES strategy_runs (id),
        FOREIGN KEY(portfolio_id) REFERENCES portfolios (id),
        FOREIGN KEY(security_id) REFERENCES securities (id)
    )
    """,
)


def ensure_strategy_schema(engine: Engine) -> None:
    """Create the Epic 4 strategy persistence tables when absent."""
    with engine.begin() as connection:
        for statement in EPIC4_SCHEMA:
            connection.execute(text(statement))
