from __future__ import annotations

import json
from typing import Any, Collection

from stratos_quant.strategy import AllocationResult


ALLOCATION_SYSTEM_PROMPT = """
You are a Senior Quantitative Portfolio Strategist auditing deterministic
allocation models. Your objective is to explain the algorithmic outputs using
the provided trend, momentum, volatility, and target-weight evidence.

Interpret the numerical evidence using these institutional KPI thresholds:
- Annualized Volatility: < 0.08 (Defensive), 0.08 to 0.15 (Moderate/Target),
  0.15 to 0.25 (High Risk), > 0.25 (Extreme Risk).
- 12-Month Momentum: < 0 (Bearish/Negative), 0.0 to 0.10 (Moderate Trend),
  > 0.10 (Strong Bullish Trend).
- Sharpe Ratio (if present): < 0.5 (Subpar), 0.5 to 1.0 (Adequate),
  1.0 to 1.5 (Good), > 1.5 (Excellent).

Explain which regimes triggered, how momentum drove selection, and specifically
how the asset's annualized volatility compared to the standard 0.15 target to
dictate its final weight sizing. Do not invent market data or guarantees. Write
concise, professional text suitable for an immutable audit log.
""".strip()


SCREENING_SYSTEM_PROMPT = """
You are an Institutional Fund Screening Assistant. Select execution candidates
only from the provided security IDs.

You are restricted to selecting ticker symbols ONLY from the provided
candidate_tickers array. Do not invent, abbreviate, or hallucinate symbols. If
you do not recommend a trade, output an empty array [].

Your selection criteria must strictly follow this investment philosophy:
1. Cost Efficiency: Strongly prefer funds with Annual Expense Ratios < 0.0020
   (0.20%). Penalize expensive funds unless they demonstrate consistently
   superior historical Alpha.
2. Risk Management: Reject or reduce target weights for funds with Max
   Drawdowns exceeding -0.25 (-25%), regardless of return.
3. Turnover Minimization: If a candidate's metrics are highly comparable to a
   security the portfolio already owns, prioritize the existing holding (HOLD)
   over a new execution (BUY) to minimize slippage and tax drag.
4. Liquidity: Favor funds with larger Total Net Assets (TNA).

Return only the requested JSON structure. Do not invent missing metrics. Provide
a clear, metric-driven rationale for each selection quoting the specific data
points used.
""".strip()


RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "security_id": {"type": "integer"},
                    "ticker": {"type": "string"},
                    "action_type": {
                        "type": "string",
                        "enum": ["BUY", "SELL", "HOLD"],
                    },
                    "target_weight": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "rationale": {"type": "string"},
                },
                "required": [
                    "security_id",
                    "ticker",
                    "action_type",
                    "target_weight",
                    "rationale",
                ],
            },
        }
    },
    "required": ["recommendations"],
}


def allocation_prompt(result: AllocationResult) -> str:
    return (
        "Audit this allocation run. Explain which regimes triggered, how "
        "12-month momentum affected selection, how annualized volatility "
        "affected sizing, and how the final targets follow from those inputs.\n\n"
        + json.dumps(result.to_dict(), indent=2, sort_keys=True)
    )


def screening_prompt(
    *,
    asset_class_code: str,
    target_weight: str,
    candidate_context: dict[str, Any],
    held_security_ids: Collection[int],
    portfolio_strategy_recommendation: str = "",
) -> str:
    candidate_tickers = [
        str(candidate.get("ticker", "")).strip().upper()
        for candidate in candidate_context.get("securities", [])
        if str(candidate.get("ticker", "")).strip()
    ]
    payload = {
        "asset_class_code": asset_class_code,
        "target_asset_class_weight": target_weight,
        "candidate_tickers": sorted(candidate_tickers),
        "held_security_ids": sorted(held_security_ids),
        "candidate_fundamentals": candidate_context,
        "portfolio_strategy_recommendation": portfolio_strategy_recommendation,
        "instructions": (
            "Choose one or more candidates. Recommendation target weights must "
            "sum exactly to the target asset-class weight. In your 'rationale' "
            "field, structure your text to explicitly state: 1. The primary "
            "performance/cost metric driving the selection. 2. Why it was "
            "chosen over competing tickers in the candidate context. Use the "
            "portfolio_strategy_recommendation as the policy anchor when it is "
            "provided, but select securities only from candidate_fundamentals."
        ),
    }
    return json.dumps(payload, indent=2, sort_keys=True)
