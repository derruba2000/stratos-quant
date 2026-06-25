from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from stratos_quant.db import create_sqlite_engine


class FundDataExtractor:
    """Extract Yahoo fund profiles, risk metrics, and performance by asset class."""

    def __init__(self, engine: Engine | None = None) -> None:
        self._engine = engine or create_sqlite_engine()

    def extract_asset_class(self, asset_class_code: str) -> dict[str, Any]:
        """Return JSON-ready baseline health data for securities in an asset class."""
        normalized_code = asset_class_code.strip().upper()
        with self._engine.connect() as connection:
            securities = connection.execute(
                text(
                    """
                    SELECT id, ticker, name, asset_class, currency_code
                    FROM securities
                    WHERE UPPER(asset_class) = :asset_class_code
                    ORDER BY ticker
                    """
                ),
                {"asset_class_code": normalized_code},
            ).mappings().all()

            results = []
            for security in securities:
                ticker = str(security["ticker"])
                profile = connection.execute(
                    text(
                        """
                        SELECT
                            family,
                            category_name,
                            legal_type,
                            description,
                            manager_name,
                            annual_expense_ratio,
                            annual_holdings_turnover,
                            total_net_assets,
                            extracted_at
                        FROM yahoo_fund_profiles
                        WHERE UPPER(symbol) = :ticker
                        ORDER BY extracted_at DESC
                        LIMIT 1
                        """
                    ),
                    {"ticker": ticker.upper()},
                ).mappings().one_or_none()

                metrics = connection.execute(
                    text(
                        """
                        SELECT metric_group, metric, value_text, value_number, extracted_at
                        FROM yahoo_fund_metrics
                        WHERE UPPER(symbol) = :ticker
                        ORDER BY metric_group, metric
                        """
                    ),
                    {"ticker": ticker.upper()},
                ).mappings().all()

                performance = connection.execute(
                    text(
                        """
                        SELECT
                            performance_type,
                            period,
                            as_of_date,
                            value,
                            category_value,
                            extracted_at
                        FROM yahoo_fund_performance
                        WHERE UPPER(symbol) = :ticker
                        ORDER BY performance_type, period
                        """
                    ),
                    {"ticker": ticker.upper()},
                ).mappings().all()

                metric_rows = [dict(metric) for metric in metrics]
                results.append(
                    {
                        "security_id": int(security["id"]),
                        "ticker": ticker,
                        "name": str(security["name"]),
                        "currency": str(security["currency_code"]),
                        "profile": dict(profile) if profile is not None else None,
                        "risk_metrics": [
                            metric
                            for metric in metric_rows
                            if self._is_risk_metric(str(metric["metric"]))
                        ],
                        "metrics": metric_rows,
                        "performance": [dict(row) for row in performance],
                    }
                )

        return {
            "asset_class_code": normalized_code,
            "security_count": len(results),
            "securities": results,
        }

    def extract_asset_class_json(
        self,
        asset_class_code: str,
        *,
        indent: int | None = None,
    ) -> str:
        """Serialize an asset class extraction for downstream prompt context."""
        return json.dumps(
            self.extract_asset_class(asset_class_code),
            indent=indent,
            sort_keys=True,
        )

    @staticmethod
    def _is_risk_metric(metric_name: str) -> bool:
        normalized = "".join(character for character in metric_name.lower() if character.isalnum())
        return "alpha" in normalized or "standarddeviation" in normalized
