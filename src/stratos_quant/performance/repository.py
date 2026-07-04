from __future__ import annotations

import json
from decimal import Decimal
import sqlite3

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .models import PerformanceReport

sqlite3.register_adapter(Decimal, str)


SCHEMA = """
CREATE TABLE IF NOT EXISTS performance_kpi_snapshots (
    id INTEGER NOT NULL PRIMARY KEY,
    snapshot_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scope VARCHAR(32) NOT NULL,
    as_of DATE NOT NULL,
    benchmark VARCHAR(64),
    risk_free_rate REAL NOT NULL,
    total_return REAL NOT NULL,
    cagr REAL NOT NULL,
    volatility REAL NOT NULL,
    max_drawdown REAL NOT NULL,
    sharpe_ratio REAL,
    sortino_ratio REAL,
    calmar_ratio REAL,
    information_ratio REAL,
    turnover REAL NOT NULL,
    total_fees DECIMAL(32, 10) NOT NULL,
    total_slippage DECIMAL(32, 10) NOT NULL,
    flags TEXT NOT NULL,
    payload_json TEXT NOT NULL
)
"""


class PerformanceKPIRepository:
    """Persist KPI reports as historical immutable snapshots."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        with self._engine.begin() as connection:
            connection.execute(text(SCHEMA))

    def save(self, report: PerformanceReport) -> int:
        payload = report.to_dict()
        with self._engine.begin() as connection:
            snapshot_id = connection.execute(
                text(
                    """
                    INSERT INTO performance_kpi_snapshots
                        (scope, as_of, benchmark, risk_free_rate, total_return,
                         cagr, volatility, max_drawdown, sharpe_ratio,
                         sortino_ratio, calmar_ratio, information_ratio,
                         turnover, total_fees, total_slippage, flags, payload_json)
                    VALUES
                        (:scope, :as_of, :benchmark, :risk_free_rate, :total_return,
                         :cagr, :volatility, :max_drawdown, :sharpe_ratio,
                         :sortino_ratio, :calmar_ratio, :information_ratio,
                         :turnover, :total_fees, :total_slippage, :flags,
                         :payload_json)
                    """
                ),
                {
                    "scope": report.scope,
                    "as_of": report.as_of.isoformat(),
                    "benchmark": report.benchmark,
                    "risk_free_rate": report.risk_free_rate,
                    "total_return": report.returns.total_return,
                    "cagr": report.returns.cagr,
                    "volatility": report.risk.volatility,
                    "max_drawdown": report.risk.max_drawdown,
                    "sharpe_ratio": report.risk_adjusted.sharpe_ratio,
                    "sortino_ratio": report.risk_adjusted.sortino_ratio,
                    "calmar_ratio": report.risk_adjusted.calmar_ratio,
                    "information_ratio": report.risk_adjusted.information_ratio,
                    "turnover": report.trading_costs.turnover,
                    "total_fees": report.trading_costs.total_fees,
                    "total_slippage": report.trading_costs.total_slippage,
                    "flags": "\n".join(report.flags),
                    "payload_json": json.dumps(payload, default=str),
                },
            ).lastrowid
        return int(snapshot_id)

    def latest(self, *, scope: str | None = None) -> dict | None:
        query = "SELECT * FROM performance_kpi_snapshots"
        params = {}
        if scope is not None:
            query += " WHERE scope = :scope"
            params["scope"] = scope.upper()
        query += " ORDER BY id DESC LIMIT 1"
        with self._engine.connect() as connection:
            row = connection.execute(text(query), params).mappings().one_or_none()
        return dict(row) if row is not None else None
