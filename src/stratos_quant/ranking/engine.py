from __future__ import annotations

import json
from decimal import Decimal
from typing import Iterable, Mapping

from stratos_quant.llm.client import ChatClient

from .models import RankedDecision, RankingCandidate, RankingGoal, RankingResult


RANKING_SYSTEM_PROMPT = """
You are an AI portfolio decision assistant. Explain rankings only from the
provided KPI evidence, portfolio goal, scores, and warnings. Do not invent
returns, risks, securities, or guarantees. Be concise and decision-oriented.
""".strip()


class AIRankingEngine:
    """Rank strategies, assets, or allocations using goal-weighted evidence."""

    def __init__(self, client: ChatClient | None = None) -> None:
        self.client = client

    def rank(
        self,
        candidates: Iterable[RankingCandidate],
        *,
        goal: RankingGoal | None = None,
        use_llm: bool = False,
    ) -> RankingResult:
        resolved_goal = goal or RankingGoal()
        candidate_list = tuple(candidates)
        if not candidate_list:
            return RankingResult(
                goal=resolved_goal,
                decisions=(),
                explanation="No candidates were supplied for ranking.",
            )

        components_by_id = {
            candidate.candidate_id: _component_scores(candidate)
            for candidate in candidate_list
        }
        weighted = resolved_goal.resolved_weights()
        scored = []
        for candidate in candidate_list:
            components = components_by_id[candidate.candidate_id]
            score = sum(
                weighted.get(component, 0.0) * value
                for component, value in components.items()
            )
            warnings = _warnings(candidate, resolved_goal)
            scored.append((score, candidate, components, warnings))

        scored.sort(
            key=lambda item: (
                item[0],
                item[2].get("return", 0.0),
                item[2].get("risk", 0.0),
                item[1].name,
            ),
            reverse=True,
        )
        decisions = []
        for rank, (score, candidate, components, warnings) in enumerate(scored, start=1):
            rationale = _deterministic_rationale(candidate, components, warnings)
            decisions.append(
                RankedDecision(
                    rank=rank,
                    candidate_id=candidate.candidate_id,
                    name=candidate.name,
                    candidate_type=candidate.candidate_type.upper(),
                    score=round(score, 6),
                    component_scores={
                        key: round(value, 6)
                        for key, value in components.items()
                    },
                    warnings=warnings,
                    rationale=rationale,
                )
            )

        result = RankingResult(
            goal=resolved_goal,
            decisions=tuple(decisions),
            explanation=_result_explanation(resolved_goal, decisions),
        )
        if use_llm and self.client is not None:
            return _with_llm_rationales(self.client, result, candidate_list)
        return result

    def rank_strategies(
        self,
        reports: Mapping[str, object],
        *,
        goal: RankingGoal | None = None,
        use_llm: bool = False,
    ) -> RankingResult:
        return self.rank(
            (
                RankingCandidate(
                    candidate_id=str(strategy_id),
                    name=str(strategy_id),
                    candidate_type="STRATEGY",
                    performance=report,
                )
                for strategy_id, report in reports.items()
            ),
            goal=goal,
            use_llm=use_llm,
        )

    def rank_assets(
        self,
        assets: Iterable[Mapping[str, object]],
        *,
        goal: RankingGoal | None = None,
        use_llm: bool = False,
    ) -> RankingResult:
        return self.rank(
            (
                RankingCandidate(
                    candidate_id=str(item.get("symbol") or item.get("ticker")),
                    name=str(item.get("name") or item.get("symbol") or item.get("ticker")),
                    candidate_type="ASSET",
                    metrics={
                        key: value
                        for key, value in item.items()
                        if key not in {"symbol", "ticker", "name"}
                    },
                    metadata=item,
                )
                for item in assets
            ),
            goal=goal,
            use_llm=use_llm,
        )

    def rank_allocations(
        self,
        allocations: Iterable[RankingCandidate],
        *,
        goal: RankingGoal | None = None,
        use_llm: bool = False,
    ) -> RankingResult:
        return self.rank(allocations, goal=goal, use_llm=use_llm)


def _component_scores(candidate: RankingCandidate) -> dict[str, float]:
    if candidate.performance is not None:
        report = candidate.performance
        return {
            "return": _bounded_positive(report.returns.cagr, scale=0.75),
            "risk": _risk_score(
                volatility=report.risk.volatility,
                max_drawdown=report.risk.max_drawdown,
            ),
            "benchmark": _benchmark_score(report.benchmark_kpis.excess_return),
            "cost": _cost_score(
                fee_drag=report.trading_costs.fee_drag,
                turnover=report.trading_costs.annualized_turnover,
            ),
            "diversification": _diversification_score(
                concentration=report.allocation.concentration_risk_flag,
                cash_exposure=report.allocation.cash_exposure,
                breaches=len(report.allocation.constraint_breaches),
            ),
        }

    metrics = {key: _float(value) for key, value in (candidate.metrics or {}).items()}
    return {
        "return": _bounded_positive(
            metrics.get("expected_return")
            or metrics.get("momentum_score")
            or metrics.get("cagr")
            or 0.0,
            scale=0.30,
        ),
        "risk": _risk_score(
            volatility=metrics.get("volatility") or metrics.get("annualized_volatility") or 0.0,
            max_drawdown=metrics.get("max_drawdown") or metrics.get("drawdown") or 0.0,
        ),
        "benchmark": _benchmark_score(metrics.get("excess_return")),
        "cost": _cost_score(
            fee_drag=metrics.get("fee_drag") or metrics.get("expense_ratio") or 0.0,
            turnover=metrics.get("turnover") or 0.0,
        ),
        "diversification": _asset_diversification_score(metrics),
    }


def _warnings(candidate: RankingCandidate, goal: RankingGoal) -> tuple[str, ...]:
    warnings: list[str] = []
    if candidate.performance is not None:
        report = candidate.performance
        warnings.extend(report.flags)
        if (
            goal.max_turnover is not None
            and report.trading_costs.annualized_turnover > goal.max_turnover
        ):
            warnings.append("Turnover exceeds portfolio goal.")
        if (
            goal.max_drawdown is not None
            and abs(report.risk.max_drawdown) > abs(goal.max_drawdown)
        ):
            warnings.append("Drawdown exceeds portfolio goal.")
        if (
            goal.min_excess_return is not None
            and (report.benchmark_kpis.excess_return or 0.0) < goal.min_excess_return
        ):
            warnings.append("Excess return is below portfolio goal.")
    else:
        metrics = {key: _float(value) for key, value in (candidate.metrics or {}).items()}
        if goal.max_drawdown is not None and abs(metrics.get("max_drawdown", 0.0)) > abs(goal.max_drawdown):
            warnings.append("Asset drawdown exceeds portfolio goal.")
        if metrics.get("constraint_breach"):
            warnings.append("Candidate has a constraint breach.")
    return tuple(dict.fromkeys(warnings))


def _deterministic_rationale(
    candidate: RankingCandidate,
    components: Mapping[str, float],
    warnings: tuple[str, ...],
) -> str:
    best_component = max(components.items(), key=lambda item: item[1])[0]
    weakest_component = min(components.items(), key=lambda item: item[1])[0]
    warning_text = (
        f" Warnings: {'; '.join(warnings)}"
        if warnings
        else " No hard warnings were detected."
    )
    return (
        f"{candidate.name} ranks on {best_component} strength, with "
        f"{weakest_component} as the weakest component.{warning_text}"
    )


def _result_explanation(
    goal: RankingGoal,
    decisions: list[RankedDecision],
) -> str:
    weights = goal.resolved_weights()
    leader = decisions[0].name if decisions else "no candidate"
    return (
        f"Ranked {len(decisions)} candidate(s) for {goal.objective.upper()} "
        f"using goal weights {weights}. Top candidate: {leader}."
    )


def _with_llm_rationales(
    client: ChatClient,
    result: RankingResult,
    candidates: tuple[RankingCandidate, ...],
) -> RankingResult:
    payload = {
        "goal": result.to_dict()["goal"],
        "decisions": [decision.to_dict() for decision in result.decisions],
        "candidate_evidence": [candidate.evidence() for candidate in candidates],
        "instructions": (
            "Explain why the ranking fits the goal. Preserve the ranks and do "
            "not change scores. Return concise prose."
        ),
    }
    rationale = client.chat(
        system_prompt=RANKING_SYSTEM_PROMPT,
        user_prompt=json.dumps(payload, indent=2, sort_keys=True, default=str),
    )
    return RankingResult(
        goal=result.goal,
        decisions=result.decisions,
        explanation=rationale,
    )


def _float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bounded_positive(value: float, *, scale: float) -> float:
    return max(0.0, min(1.0, value / scale))


def _risk_score(*, volatility: float, max_drawdown: float) -> float:
    drawdown_penalty = min(1.0, abs(max_drawdown) / 0.40)
    volatility_penalty = min(1.0, volatility / 0.35)
    return max(0.0, 1.0 - (0.55 * drawdown_penalty + 0.45 * volatility_penalty))


def _benchmark_score(excess_return: float | None) -> float:
    if excess_return is None:
        return 0.50
    return max(0.0, min(1.0, 0.50 + excess_return / 0.20))


def _cost_score(*, fee_drag: float, turnover: float) -> float:
    fee_penalty = min(1.0, fee_drag / 0.05)
    turnover_penalty = min(1.0, turnover / 5.0)
    return max(0.0, 1.0 - (0.65 * fee_penalty + 0.35 * turnover_penalty))


def _diversification_score(*, concentration: bool, cash_exposure: float, breaches: int) -> float:
    concentration_penalty = 0.35 if concentration else 0.0
    cash_penalty = min(0.25, abs(cash_exposure - 0.05))
    breach_penalty = min(0.40, breaches * 0.15)
    return max(0.0, 1.0 - concentration_penalty - cash_penalty - breach_penalty)


def _asset_diversification_score(metrics: Mapping[str, float]) -> float:
    correlation = abs(metrics.get("correlation_to_portfolio", 0.0))
    weight = metrics.get("current_weight", metrics.get("target_weight", 0.0))
    concentration_penalty = min(0.50, weight)
    correlation_penalty = min(0.45, correlation * 0.45)
    return max(0.0, 1.0 - concentration_penalty - correlation_penalty)
