from __future__ import annotations

import json
from typing import Any, Collection

from stratos_quant.strategy import AllocationResult


ALLOCATION_SYSTEM_PROMPT = """
You are a portfolio strategy auditor. Explain deterministic model output only.
Use the supplied trend, momentum, volatility, and target-weight evidence.
Do not invent market data, returns, securities, or guarantees. Write concise
plain text suitable for an immutable strategy audit log.
""".strip()


SCREENING_SYSTEM_PROMPT = """
You are a fund screening assistant. Select execution candidates only from the
provided security IDs. Compare annual expense ratios, net assets, risk metrics,
performance, and whether the portfolio already owns each security. Return only
the requested JSON structure. Do not invent missing metrics.
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
) -> str:
    payload = {
        "asset_class_code": asset_class_code,
        "target_asset_class_weight": target_weight,
        "held_security_ids": sorted(held_security_ids),
        "candidate_fundamentals": candidate_context,
        "instructions": (
            "Choose one or more candidates. Recommendation target weights must "
            "sum to the target asset-class weight. Existing holdings may be "
            "HOLD; new preferred candidates may be BUY."
        ),
    }
    return json.dumps(payload, indent=2, sort_keys=True)
