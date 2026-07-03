from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any, Collection

from stratos_quant.data import FundDataExtractor, PortfolioValuationService
from stratos_quant.strategy import AllocationResult

from .client import ChatClient
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

RECOMMENDATION_WEIGHT_TOLERANCE = Decimal("0.001")


class AdvisoryPipeline:
    """Generate and persist LLM strategy audits and ticker selections."""

    def __init__(
        self,
        client: ChatClient,
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
            llm_model=self.client.settings.llm_model,
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
        portfolio_strategy_recommendation: str = "",
    ) -> tuple[SecurityRecommendation, ...]:
        response = self.client.chat_json(
            system_prompt=SCREENING_SYSTEM_PROMPT,
            user_prompt=screening_prompt(
                asset_class_code=asset_class_code,
                target_weight=format(target_weight, ".10f"),
                candidate_context=candidate_context,
                held_security_ids=held_security_ids,
                portfolio_strategy_recommendation=portfolio_strategy_recommendation,
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
        recommendations = _normalize_recommendation_weights(
            recommendations,
            target_weight,
        )

        self.repository.save_recommendations(
            run_id=run_id,
            portfolio_id=portfolio_id,
            recommendations=recommendations,
        )
        return recommendations

    def screen_portfolio_asset_class(
        self,
        *,
        run_id: int,
        portfolio_id: int,
        asset_class_code: str,
        target_weight: Decimal,
        fund_data: FundDataExtractor,
        portfolio_data: PortfolioValuationService,
        portfolio_strategy_recommendation: str = "",
    ) -> tuple[SecurityRecommendation, ...]:
        """Extract candidates and current holdings before asking Ollama to screen."""
        context = fund_data.extract_asset_class(asset_class_code)
        valuation = portfolio_data.value_portfolio(portfolio_id, strict=False)
        held_security_ids = {
            holding.security_id
            for holding in valuation.holdings
            if holding.quantity != 0
        }
        return self.screen_asset_class(
            run_id=run_id,
            portfolio_id=portfolio_id,
            asset_class_code=asset_class_code,
            target_weight=target_weight,
            candidate_context=context,
            held_security_ids=held_security_ids,
            portfolio_strategy_recommendation=portfolio_strategy_recommendation,
        )


def _normalize_recommendation_weights(
    recommendations: tuple[SecurityRecommendation, ...],
    target_weight: Decimal,
) -> tuple[SecurityRecommendation, ...]:
    total_weight = sum(
        (recommendation.target_weight for recommendation in recommendations),
        Decimal("0"),
    )
    if total_weight == 0 and target_weight > 0:
        adjusted = list(recommendations)
        adjusted[0] = replace(
            adjusted[0],
            target_weight=target_weight,
        )
        return tuple(adjusted)

    residual = target_weight - total_weight
    if residual == 0:
        return recommendations
    if abs(residual) > RECOMMENDATION_WEIGHT_TOLERANCE:
        raise OllamaResponseError(
            "Recommendation target weights do not equal the asset-class target "
            f"(sum={total_weight}, target={target_weight})"
        )

    adjustable_index = max(
        range(len(recommendations)),
        key=lambda index: recommendations[index].target_weight,
    )
    adjusted_weight = recommendations[adjustable_index].target_weight + residual
    if adjusted_weight < Decimal("0"):
        raise OllamaResponseError(
            "Recommendation target weights do not equal the asset-class target "
            f"(sum={total_weight}, target={target_weight})"
        )

    adjusted = list(recommendations)
    adjusted[adjustable_index] = replace(
        adjusted[adjustable_index],
        target_weight=adjusted_weight,
    )
    return tuple(adjusted)
