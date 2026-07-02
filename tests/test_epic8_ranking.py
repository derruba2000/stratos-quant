from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

from stratos_quant.performance import PerformanceKPIEngine
from stratos_quant.ranking import AIRankingEngine, RankingCandidate, RankingGoal


class FakeRankingClient:
    def __init__(self):
        self.settings = SimpleNamespace(llm_model="fake-ranker")
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return "LLM rationale: Growth strategy wins because its KPI evidence best fits the goal."


def _report(*, slope: float, drawdown: float, fees: int = 0):
    dates = pd.bdate_range("2026-01-01", periods=80)
    values = []
    benchmark = []
    for index, timestamp in enumerate(dates):
        shock = drawdown if 30 <= index <= 38 else 0
        values.append(10000 + index * slope + shock)
        benchmark.append(10000 + index * 12)
    curve = pd.DataFrame(
        {
            "timestamp": dates,
            "equity": values,
            "fees_paid": [0] * 40 + [fees] * 40,
            "slippage_paid": [0] * 40 + [fees // 2] * 40,
            "benchmark_equity": benchmark,
        }
    )
    trades = pd.DataFrame(
        [
            {
                "timestamp": "2026-01-02",
                "ticker": "AAA",
                "trade_value": Decimal("1000"),
                "estimated_fees": Decimal(str(fees)),
                "estimated_slippage": Decimal(str(fees // 2)),
            }
        ]
    )
    positions = pd.DataFrame(
        [
            {
                "timestamp": dates[0],
                "ticker": "AAA",
                "asset_class_code": "EQUITY",
                "weight": 0.45,
            },
            {
                "timestamp": dates[0],
                "ticker": "BBB",
                "asset_class_code": "BOND",
                "weight": 0.45,
            },
        ]
    )
    return PerformanceKPIEngine().calculate(
        curve,
        trades=trades,
        positions=positions,
        benchmark="SPY",
        rebalance_frequency="MONTHLY",
    )


def test_epic8_ranks_strategies_by_portfolio_goal_and_performance():
    growth = _report(slope=35, drawdown=-700, fees=5)
    defensive = _report(slope=18, drawdown=-80, fees=2)

    growth_result = AIRankingEngine().rank_strategies(
        {"Growth": growth, "Defensive": defensive},
        goal=RankingGoal(objective="GROWTH"),
    )
    preservation_result = AIRankingEngine().rank_strategies(
        {"Growth": growth, "Defensive": defensive},
        goal=RankingGoal(objective="CAPITAL_PRESERVATION"),
    )

    assert growth_result.winner.name == "Growth"
    assert preservation_result.winner.name == "Defensive"
    assert growth_result.decisions[0].component_scores["return"] > growth_result.decisions[1].component_scores["return"]
    assert preservation_result.decisions[0].component_scores["risk"] > preservation_result.decisions[1].component_scores["risk"]


def test_epic8_ranks_assets_and_flags_goal_violations():
    result = AIRankingEngine().rank_assets(
        [
            {
                "symbol": "FAST",
                "momentum_score": 0.24,
                "annualized_volatility": 0.35,
                "max_drawdown": -0.30,
                "expense_ratio": 0.006,
                "correlation_to_portfolio": 0.80,
            },
            {
                "symbol": "STEADY",
                "momentum_score": 0.12,
                "annualized_volatility": 0.08,
                "max_drawdown": -0.05,
                "expense_ratio": 0.001,
                "correlation_to_portfolio": 0.20,
            },
        ],
        goal=RankingGoal(objective="CAPITAL_PRESERVATION", max_drawdown=0.10),
    )

    assert result.winner.candidate_id == "STEADY"
    assert any("drawdown" in warning.lower() for warning in result.decisions[1].warnings)
    assert "Top candidate: STEADY" in result.explanation


def test_epic8_ranks_allocations_and_can_request_llm_explanation():
    client = FakeRankingClient()
    candidate = RankingCandidate(
        candidate_id="alloc-1",
        name="Core Allocation",
        candidate_type="ALLOCATION",
        performance=_report(slope=20, drawdown=-100, fees=1),
    )

    result = AIRankingEngine(client).rank_allocations(
        [candidate],
        goal=RankingGoal(objective="BALANCED"),
        use_llm=True,
    )

    assert result.winner.candidate_id == "alloc-1"
    assert result.explanation.startswith("LLM rationale")
    assert client.calls
    assert "candidate_evidence" in client.calls[0]["user_prompt"]
