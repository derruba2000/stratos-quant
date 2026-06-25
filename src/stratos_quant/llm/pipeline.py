from __future__ import annotations

from decimal import Decimal
from typing import Any, Collection

from stratos_quant.strategy import AllocationResult

from .client import OllamaClient
from .errors import OllamaResponseError
from .models import SecurityRecommendation
from .prompts import (
    ALLOCATION_SYSTEM_PROMPT,
    RECOMMENDATION_SCHEMA,
    SCREENING_SYSTEM_PROMPT,
    allocation_prompt,
    screening_prompt,
)
from .repository import StrategyRepository


class AdvisoryPipeline:
    """Generate and persist Ollama strategy audits and ticker selections."""

    def __init__(
        self,
        client: OllamaClient,
        repository: StrategyRepository,
    ) -> None:
        self.client = client
        self.repository = repository

    def rationalize_allocation(
        self,
        *,
        portfolio_id: int,
        allocation: AllocationResult,
    ) -> int:
        rationale = self.client.chat(
            system_prompt=ALLOCATION_SYSTEM_PROMPT,
            user_prompt=allocation_prompt(allocation),
        )
        return self.repository.create_run(
            portfolio_id=portfolio_id,
            allocation=allocation,
            llm_model=self.client.settings.ollama_model,
            rationale=rationale,
        )

    def screen_asset_class(
        self,
        *,
        run_id: int,
        portfolio_id: int,
        asset_class_code: str,
        target_weight: Decimal,
        candidate_context: dict[str, Any],
        held_security_ids: Collection[int] = (),
    ) -> tuple[SecurityRecommendation, ...]:
        response = self.client.chat_json(
            system_prompt=SCREENING_SYSTEM_PROMPT,
            user_prompt=screening_prompt(
                asset_class_code=asset_class_code,
                target_weight=format(target_weight, ".10f"),
                candidate_context=candidate_context,
                held_security_ids=held_security_ids,
            ),
            response_schema=RECOMMENDATION_SCHEMA,
        )
        raw_recommendations = response.get("recommendations")
        if not isinstance(raw_recommendations, list) or not raw_recommendations:
            raise OllamaResponseError("Ollama returned no security recommendations")
        try:
            recommendations = tuple(
                SecurityRecommendation.from_dict(item)
                for item in raw_recommendations
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OllamaResponseError(
                f"Ollama returned an invalid recommendation: {exc}"
            ) from exc

        candidates = {
            int(item["security_id"]): str(item["ticker"]).upper()
            for item in candidate_context.get("securities", [])
        }
        for recommendation in recommendations:
            if (
                recommendation.security_id not in candidates
                or candidates[recommendation.security_id]
                != recommendation.ticker.upper()
            ):
                raise OllamaResponseError(
                    f"Recommendation is not in candidate context: "
                    f"{recommendation.ticker}"
                )
        total_weight = sum(
            (recommendation.target_weight for recommendation in recommendations),
            Decimal("0"),
        )
        if total_weight != target_weight:
            raise OllamaResponseError(
                "Recommendation target weights do not equal the asset-class target"
            )

        self.repository.save_recommendations(
            run_id=run_id,
            portfolio_id=portfolio_id,
            recommendations=recommendations,
        )
        return recommendations
