from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from stratos_quant.db import ensure_strategy_schema
from stratos_quant.strategy import AllocationResult

from .models import SecurityRecommendation


class StrategyRepository:
    """Persist strategy runs and LLM advisory output."""

    def __init__(self, engine: Engine, *, initialize_schema: bool = True) -> None:
        self._engine = engine
        if initialize_schema:
            ensure_strategy_schema(engine)

    def create_run(
        self,
        *,
        portfolio_id: int,
        allocation: AllocationResult,
        llm_model: str,
        rationale: str,
    ) -> int:
        with self._engine.begin() as connection:
            run_id = connection.execute(
                text(
                    """
                    INSERT INTO strategy_runs
                        (allocation_model, llm_model_used, llm_overall_rationale)
                    VALUES (:allocation_model, :llm_model, :rationale)
                    """
                ),
                {
                    "allocation_model": allocation.model,
                    "llm_model": llm_model,
                    "rationale": rationale,
                },
            ).lastrowid
            for asset_class_code, target_weight in allocation.weights.items():
                connection.execute(
                    text(
                        """
                        INSERT INTO strategy_target_allocations
                            (run_id, portfolio_id, asset_class_code, target_weight)
                        VALUES
                            (:run_id, :portfolio_id, :asset_class_code, :target_weight)
                        """
                    ),
                    {
                        "run_id": run_id,
                        "portfolio_id": portfolio_id,
                        "asset_class_code": asset_class_code,
                        "target_weight": target_weight,
                    },
                )
            for signal in allocation.signals:
                connection.execute(
                    text(
                        """
                        INSERT INTO strategy_allocation_signals
                            (run_id, signal_scope, asset_class_code, security_id,
                             ticker, trend_positive, momentum_12m,
                             annualized_volatility, security_count)
                        VALUES
                            (:run_id, 'ASSET_CLASS', :asset_class_code, NULL,
                             NULL, :trend_positive, :momentum_12m,
                             :annualized_volatility, :security_count)
                        """
                    ),
                    {
                        "run_id": run_id,
                        "asset_class_code": signal.asset_class_code,
                        "trend_positive": signal.trend_positive,
                        "momentum_12m": signal.momentum_12m,
                        "annualized_volatility": signal.annualized_volatility,
                        "security_count": signal.security_count,
                    },
                )
            for signal in allocation.security_signals:
                connection.execute(
                    text(
                        """
                        INSERT INTO strategy_allocation_signals
                            (run_id, signal_scope, asset_class_code, security_id,
                             ticker, trend_positive, momentum_12m,
                             annualized_volatility, security_count)
                        VALUES
                            (:run_id, 'SECURITY', :asset_class_code, :security_id,
                             :ticker, :trend_positive, :momentum_12m,
                             :annualized_volatility, 1)
                        """
                    ),
                    {
                        "run_id": run_id,
                        "asset_class_code": signal.asset_class_code,
                        "security_id": signal.security_id,
                        "ticker": signal.ticker,
                        "trend_positive": signal.trend_positive,
                        "momentum_12m": signal.momentum_12m,
                        "annualized_volatility": signal.annualized_volatility,
                    },
                )
        return int(run_id)

    def save_recommendations(
        self,
        *,
        run_id: int,
        portfolio_id: int,
        recommendations: Iterable[SecurityRecommendation],
    ) -> None:
        with self._engine.begin() as connection:
            for recommendation in recommendations:
                connection.execute(
                    text(
                        """
                        INSERT INTO asset_recommendations
                            (run_id, portfolio_id, security_id, action_type,
                             target_weight, estimated_trade_value,
                             recommendation_timestamp, llm_security_rationale,
                             is_executed)
                        VALUES
                            (:run_id, :portfolio_id, :security_id, :action_type,
                             :target_weight, :estimated_trade_value,
                             CURRENT_TIMESTAMP, :rationale, 0)
                        """
                    ),
                    {
                        "run_id": run_id,
                        "portfolio_id": portfolio_id,
                        "security_id": recommendation.security_id,
                        "action_type": recommendation.action_type,
                        "target_weight": recommendation.target_weight,
                        "estimated_trade_value": Decimal("0"),
                        "rationale": recommendation.rationale,
                    },
                )

    def get_run(self, run_id: int) -> dict[str, object]:
        """Return one strategy run for dashboard rendering."""
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM strategy_runs WHERE id = :run_id"),
                {"run_id": run_id},
            ).mappings().one()
        return dict(row)

    def get_recommendations(
        self,
        *,
        run_id: int,
        portfolio_id: int,
    ) -> list[dict[str, object]]:
        """Return recommendations enriched with security labels."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT
                        ar.id,
                        ar.action_type,
                        ar.target_weight,
                        ar.estimated_trade_value,
                        ar.llm_security_rationale,
                        ar.is_executed,
                        s.ticker,
                        s.name,
                        s.asset_class
                    FROM asset_recommendations ar
                    JOIN securities s ON s.id = ar.security_id
                    WHERE ar.run_id = :run_id
                      AND ar.portfolio_id = :portfolio_id
                    ORDER BY ar.id
                    """
                ),
                {"run_id": run_id, "portfolio_id": portfolio_id},
            ).mappings().all()
        return [dict(row) for row in rows]

    def set_recommendation_executed(
        self,
        recommendation_id: int,
        is_executed: bool,
    ) -> None:
        """Update the execution state of a generated trade."""
        with self._engine.begin() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE asset_recommendations
                    SET is_executed = :is_executed
                    WHERE id = :recommendation_id
                    """
                ),
                {
                    "recommendation_id": recommendation_id,
                    "is_executed": bool(is_executed),
                },
            )
            if result.rowcount != 1:
                raise ValueError(
                    f"Recommendation does not exist: {recommendation_id}"
                )
