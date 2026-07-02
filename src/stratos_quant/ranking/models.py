from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from stratos_quant.performance import PerformanceReport


DEFAULT_GOAL_WEIGHTS = {
    "return": 0.30,
    "risk": 0.25,
    "benchmark": 0.20,
    "cost": 0.15,
    "diversification": 0.10,
}

GOAL_PRESETS = {
    "GROWTH": {
        "return": 0.45,
        "risk": 0.15,
        "benchmark": 0.20,
        "cost": 0.10,
        "diversification": 0.10,
    },
    "BALANCED": DEFAULT_GOAL_WEIGHTS,
    "CAPITAL_PRESERVATION": {
        "return": 0.15,
        "risk": 0.40,
        "benchmark": 0.15,
        "cost": 0.15,
        "diversification": 0.15,
    },
    "LOW_COST": {
        "return": 0.20,
        "risk": 0.20,
        "benchmark": 0.15,
        "cost": 0.35,
        "diversification": 0.10,
    },
}


@dataclass(frozen=True, slots=True)
class RankingGoal:
    objective: str = "BALANCED"
    weights: Mapping[str, float] | None = None
    risk_tolerance: str = "MEDIUM"
    max_turnover: float | None = None
    max_drawdown: float | None = None
    min_excess_return: float | None = None

    def resolved_weights(self) -> dict[str, float]:
        raw = dict(GOAL_PRESETS.get(self.objective.upper(), DEFAULT_GOAL_WEIGHTS))
        raw.update(self.weights or {})
        total = sum(max(0.0, value) for value in raw.values())
        if total <= 0:
            raise ValueError("At least one positive goal weight is required")
        return {
            key: max(0.0, value) / total
            for key, value in raw.items()
        }


@dataclass(frozen=True, slots=True)
class RankingCandidate:
    candidate_id: str
    name: str
    candidate_type: str
    performance: PerformanceReport | None = None
    metrics: Mapping[str, float | Decimal | int | bool | None] | None = None
    metadata: Mapping[str, Any] | None = None

    def evidence(self) -> dict[str, Any]:
        payload = {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "candidate_type": self.candidate_type,
            "metrics": dict(self.metrics or {}),
            "metadata": dict(self.metadata or {}),
        }
        if self.performance is not None:
            payload["performance"] = self.performance.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class RankedDecision:
    rank: int
    candidate_id: str
    name: str
    candidate_type: str
    score: float
    component_scores: Mapping[str, float]
    warnings: tuple[str, ...]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "candidate_id": self.candidate_id,
            "name": self.name,
            "candidate_type": self.candidate_type,
            "score": self.score,
            "component_scores": dict(self.component_scores),
            "warnings": list(self.warnings),
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class RankingResult:
    goal: RankingGoal
    decisions: tuple[RankedDecision, ...]
    explanation: str

    @property
    def winner(self) -> RankedDecision | None:
        return self.decisions[0] if self.decisions else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": {
                "objective": self.goal.objective,
                "weights": self.goal.resolved_weights(),
                "risk_tolerance": self.goal.risk_tolerance,
                "max_turnover": self.goal.max_turnover,
                "max_drawdown": self.goal.max_drawdown,
                "min_excess_return": self.goal.min_excess_return,
            },
            "decisions": [decision.to_dict() for decision in self.decisions],
            "explanation": self.explanation,
        }
