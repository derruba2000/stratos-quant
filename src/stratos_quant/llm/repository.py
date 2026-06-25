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
                             llm_security_rationale, is_executed)
                        VALUES
                            (:run_id, :portfolio_id, :security_id, :action_type,
                             :target_weight, :estimated_trade_value,
                             :rationale, 0)
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
