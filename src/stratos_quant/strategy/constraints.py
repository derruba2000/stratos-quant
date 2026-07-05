from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any, Mapping, Sequence

from .errors import AllocationConstraintError
from .models import ONE, WEIGHT_PRECISION, ZERO, normalize_weights


@dataclass(frozen=True, slots=True)
class AllocationConstraints:
    """Hard and soft limits for a final portfolio target."""

    cash_code: str = "CASH"
    max_single_asset_weight: Decimal | None = None
    min_single_asset_weight: Decimal | None = None
    max_asset_class_weights: Mapping[str, Decimal] | None = None
    min_asset_class_weights: Mapping[str, Decimal] | None = None
    min_cash_weight: Decimal | None = None
    soft_max_single_asset_weight: Decimal | None = None
    soft_max_asset_class_weights: Mapping[str, Decimal] | None = None
    drift_limit: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ConstraintAdjustment:
    constraint_name: str
    scope: str
    code: str
    before_weight: Decimal
    after_weight: Decimal
    redistributed_weight: Decimal
    hard: bool
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_name": self.constraint_name,
            "scope": self.scope,
            "code": self.code,
            "before_weight": self.before_weight,
            "after_weight": self.after_weight,
            "redistributed_weight": self.redistributed_weight,
            "hard": self.hard,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class ConstrainedAllocationResult:
    weights: Mapping[str, Decimal]
    adjustments: tuple[ConstraintAdjustment, ...]
    warnings: tuple[str, ...]
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": {
                code: format(weight, ".10f")
                for code, weight in sorted(self.weights.items())
            },
            "adjustments": [
                adjustment.to_dict() for adjustment in self.adjustments
            ],
            "warnings": list(self.warnings),
            "explanation": self.explanation,
        }


class PortfolioRebalanceEngine:
    """Apply Epic 4 rebalance and risk constraints to target weights."""

    def __init__(self, constraints: AllocationConstraints | None = None) -> None:
        self.constraints = constraints or AllocationConstraints()

    def rebalance(
        self,
        proposed_weights: Mapping[str, Decimal | float | str],
        *,
        asset_class_map: Mapping[str, str] | None = None,
        rankings: Sequence[str] | Mapping[str, int | float] | None = None,
        baseline_weights: Mapping[str, Decimal] | None = None,
    ) -> ConstrainedAllocationResult:
        """Return a valid 100% allocation with hard limits enforced.

        ``rankings`` is highest first when passed as a sequence. Higher numeric
        scores are preferred when passed as a mapping.
        """
        constraints = _normalize_constraints(self.constraints)
        cash_code = constraints.cash_code
        weights = normalize_weights(
            {
                _code(code): _decimal(weight)
                for code, weight in proposed_weights.items()
                if _decimal(weight) > ZERO
            }
        )

        if baseline_weights is not None and constraints.drift_limit is not None:
            self._apply_drift_clipping(weights, baseline_weights)

        if cash_code not in weights and (
            constraints.min_cash_weight is not None
            or constraints.max_single_asset_weight is not None
            or constraints.max_asset_class_weights
        ):
            weights[cash_code] = ZERO

        asset_classes = {
            _code(code): _code(class_code)
            for code, class_code in (asset_class_map or {}).items()
        }
        for code in weights:
            asset_classes.setdefault(code, code)

        rank_scores = _rank_scores(rankings)
        warnings = _soft_constraint_warnings(
            weights,
            asset_classes,
            constraints,
        )
        adjustments: list[ConstraintAdjustment] = []

        minimums = _minimum_weights(weights, constraints)
        max_single = constraints.max_single_asset_weight
        for code, floor in minimums.items():
            if max_single is not None and floor > max_single:
                raise AllocationConstraintError(
                    f"Minimum weight for {code} exceeds max single asset weight"
                )
        if sum(minimums.values(), ZERO) > ONE:
            raise AllocationConstraintError("Minimum allocation requirements exceed 100%")

        _raise_minimums(
            weights,
            minimums,
            rank_scores,
            adjustments,
        )
        _apply_single_asset_caps(
            weights,
            asset_classes,
            constraints,
            minimums,
            rank_scores,
            adjustments,
        )
        _apply_class_minimums(
            weights,
            asset_classes,
            constraints,
            minimums,
            rank_scores,
            adjustments,
        )
        _apply_class_caps(
            weights,
            asset_classes,
            constraints,
            minimums,
            rank_scores,
            adjustments,
        )
        _apply_single_asset_caps(
            weights,
            asset_classes,
            constraints,
            minimums,
            rank_scores,
            adjustments,
        )

        final_weights = _quantized_total(weights)
        _validate_final(final_weights, asset_classes, constraints, minimums)
        return ConstrainedAllocationResult(
            weights=final_weights,
            adjustments=tuple(adjustments),
            warnings=tuple(warnings),
            explanation=_explain(adjustments, warnings),
        )


def apply_allocation_constraints(
    proposed_weights: Mapping[str, Decimal | float | str],
    constraints: AllocationConstraints | None = None,
    *,
    asset_class_map: Mapping[str, str] | None = None,
    rankings: Sequence[str] | Mapping[str, int | float] | None = None,
) -> ConstrainedAllocationResult:
    """Convenience wrapper for one-off constrained rebalance calculations."""
    return PortfolioRebalanceEngine(constraints).rebalance(
        proposed_weights,
        asset_class_map=asset_class_map,
        rankings=rankings,
    )


def _normalize_constraints(
    constraints: AllocationConstraints,
) -> AllocationConstraints:
    return AllocationConstraints(
        cash_code=_code(constraints.cash_code),
        max_single_asset_weight=_optional_decimal(
            constraints.max_single_asset_weight
        ),
        min_single_asset_weight=_optional_decimal(
            constraints.min_single_asset_weight
        ),
        max_asset_class_weights={
            _code(code): _decimal(weight)
            for code, weight in (constraints.max_asset_class_weights or {}).items()
        },
        min_asset_class_weights={
            _code(code): _decimal(weight)
            for code, weight in (constraints.min_asset_class_weights or {}).items()
        },
        min_cash_weight=_optional_decimal(constraints.min_cash_weight),
        soft_max_single_asset_weight=_optional_decimal(
            constraints.soft_max_single_asset_weight
        ),
        soft_max_asset_class_weights={
            _code(code): _decimal(weight)
            for code, weight in (
                constraints.soft_max_asset_class_weights or {}
            ).items()
        },
        drift_limit=_optional_decimal(constraints.drift_limit),
    )


def _decimal(value: Decimal | float | int | str | None) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value))


def _optional_decimal(value: Decimal | float | int | str | None) -> Decimal | None:
    return None if value is None else _decimal(value)


def _code(value: str) -> str:
    return value.strip().upper()


def _rank_scores(
    rankings: Sequence[str] | Mapping[str, int | float] | None,
) -> dict[str, Decimal]:
    if rankings is None:
        return {}
    if isinstance(rankings, Mapping):
        return {_code(code): _decimal(score) for code, score in rankings.items()}
    total = len(rankings)
    return {
        _code(code): Decimal(total - index)
        for index, code in enumerate(rankings)
    }


def _minimum_weights(
    weights: Mapping[str, Decimal],
    constraints: AllocationConstraints,
) -> dict[str, Decimal]:
    minimums: dict[str, Decimal] = {}
    if constraints.min_single_asset_weight is not None:
        for code, weight in weights.items():
            if weight > ZERO:
                minimums[code] = constraints.min_single_asset_weight
    if constraints.min_cash_weight is not None:
        minimums[constraints.cash_code] = max(
            minimums.get(constraints.cash_code, ZERO),
            constraints.min_cash_weight,
        )
    return minimums


def _raise_minimums(
    weights: dict[str, Decimal],
    minimums: Mapping[str, Decimal],
    rank_scores: Mapping[str, Decimal],
    adjustments: list[ConstraintAdjustment],
) -> None:
    for code, floor in sorted(minimums.items()):
        weights.setdefault(code, ZERO)
        if weights[code] >= floor:
            continue
        before = weights[code]
        deficit = floor - before
        _fund_weight(
            weights,
            amount=deficit,
            exclude={code},
            minimums=minimums,
            rank_scores=rank_scores,
        )
        weights[code] = floor
        adjustments.append(
            ConstraintAdjustment(
                constraint_name="minimum_weight",
                scope="asset",
                code=code,
                before_weight=before,
                after_weight=floor,
                redistributed_weight=deficit,
                hard=True,
                explanation=f"Raised {code} to its required minimum weight.",
            )
        )


def _apply_single_asset_caps(
    weights: dict[str, Decimal],
    asset_classes: Mapping[str, str],
    constraints: AllocationConstraints,
    minimums: Mapping[str, Decimal],
    rank_scores: Mapping[str, Decimal],
    adjustments: list[ConstraintAdjustment],
) -> None:
    cap = constraints.max_single_asset_weight
    if cap is None:
        return
    for code in sorted(weights):
        if weights[code] <= cap:
            continue
        before = weights[code]
        excess = before - cap
        weights[code] = cap
        _redistribute_weight(
            weights,
            amount=excess,
            exclude={code},
            asset_classes=asset_classes,
            constraints=constraints,
            minimums=minimums,
            rank_scores=rank_scores,
        )
        adjustments.append(
            ConstraintAdjustment(
                constraint_name="max_single_asset_weight",
                scope="asset",
                code=code,
                before_weight=before,
                after_weight=cap,
                redistributed_weight=excess,
                hard=True,
                explanation=(
                    f"Capped {code} and redistributed the excess to eligible "
                    "assets or cash."
                ),
            )
        )


def _apply_class_minimums(
    weights: dict[str, Decimal],
    asset_classes: Mapping[str, str],
    constraints: AllocationConstraints,
    minimums: Mapping[str, Decimal],
    rank_scores: Mapping[str, Decimal],
    adjustments: list[ConstraintAdjustment],
) -> None:
    for class_code, floor in sorted(
        (constraints.min_asset_class_weights or {}).items()
    ):
        current = _class_weight(weights, asset_classes, class_code)
        if current >= floor:
            continue
        members = _class_members(weights, asset_classes, class_code)
        if not members:
            if class_code == constraints.cash_code:
                members = [constraints.cash_code]
                weights.setdefault(constraints.cash_code, ZERO)
            else:
                raise AllocationConstraintError(
                    f"No asset is available to satisfy minimum class {class_code}"
                )
        deficit = floor - current
        _fund_weight(
            weights,
            amount=deficit,
            exclude=set(members),
            minimums=minimums,
            rank_scores=rank_scores,
        )
        _distribute_to_members(
            weights,
            members,
            deficit,
            constraints,
            asset_classes,
            rank_scores,
        )
        adjustments.append(
            ConstraintAdjustment(
                constraint_name="min_asset_class_weight",
                scope="asset_class",
                code=class_code,
                before_weight=current,
                after_weight=floor,
                redistributed_weight=deficit,
                hard=True,
                explanation=f"Raised {class_code} to its required class floor.",
            )
        )


def _apply_class_caps(
    weights: dict[str, Decimal],
    asset_classes: Mapping[str, str],
    constraints: AllocationConstraints,
    minimums: Mapping[str, Decimal],
    rank_scores: Mapping[str, Decimal],
    adjustments: list[ConstraintAdjustment],
) -> None:
    for class_code, cap in sorted((constraints.max_asset_class_weights or {}).items()):
        current = _class_weight(weights, asset_classes, class_code)
        if current <= cap:
            continue
        excess = current - cap
        members = _class_members(weights, asset_classes, class_code)
        _reduce_members(
            weights,
            members,
            amount=excess,
            minimums=minimums,
            rank_scores=rank_scores,
        )
        _redistribute_weight(
            weights,
            amount=excess,
            exclude=set(members),
            asset_classes=asset_classes,
            constraints=constraints,
            minimums=minimums,
            rank_scores=rank_scores,
        )
        adjustments.append(
            ConstraintAdjustment(
                constraint_name="max_asset_class_weight",
                scope="asset_class",
                code=class_code,
                before_weight=current,
                after_weight=cap,
                redistributed_weight=excess,
                hard=True,
                explanation=(
                    f"Capped {class_code} and redistributed the excess outside "
                    "that asset class."
                ),
            )
        )


def _fund_weight(
    weights: dict[str, Decimal],
    *,
    amount: Decimal,
    exclude: set[str],
    minimums: Mapping[str, Decimal],
    rank_scores: Mapping[str, Decimal],
) -> None:
    remaining = amount
    donors = sorted(
        [code for code in weights if code not in exclude],
        key=lambda code: (rank_scores.get(code, ZERO), code),
    )
    for code in donors:
        reducible = weights[code] - minimums.get(code, ZERO)
        if reducible <= ZERO:
            continue
        take = min(reducible, remaining)
        weights[code] -= take
        remaining -= take
        if remaining == ZERO:
            return
    raise AllocationConstraintError("Constraints require more weight than available")


def _redistribute_weight(
    weights: dict[str, Decimal],
    *,
    amount: Decimal,
    exclude: set[str],
    asset_classes: Mapping[str, str],
    constraints: AllocationConstraints,
    minimums: Mapping[str, Decimal],
    rank_scores: Mapping[str, Decimal],
) -> None:
    remaining = amount
    receivers = sorted(
        [
            code for code in weights
            if code not in exclude and _capacity(
                code, weights, asset_classes, constraints
            ) > ZERO
        ],
        key=lambda code: (
            code == constraints.cash_code,
            -rank_scores.get(code, ZERO),
            code,
        ),
    )
    if constraints.cash_code not in exclude:
        weights.setdefault(constraints.cash_code, ZERO)
        if constraints.cash_code not in receivers and _capacity(
            constraints.cash_code,
            weights,
            asset_classes,
            constraints,
        ) > ZERO:
            receivers.append(constraints.cash_code)

    for code in receivers:
        capacity = _capacity(code, weights, asset_classes, constraints)
        if capacity <= ZERO:
            continue
        add = min(capacity, remaining)
        weights[code] += add
        remaining -= add
        if remaining == ZERO:
            return
    raise AllocationConstraintError("No eligible allocation can absorb residual weight")


def _distribute_to_members(
    weights: dict[str, Decimal],
    members: Sequence[str],
    amount: Decimal,
    constraints: AllocationConstraints,
    asset_classes: Mapping[str, str],
    rank_scores: Mapping[str, Decimal],
) -> None:
    remaining = amount
    for code in sorted(members, key=lambda item: (-rank_scores.get(item, ZERO), item)):
        capacity = _capacity(code, weights, asset_classes, constraints)
        if capacity <= ZERO:
            continue
        add = min(capacity, remaining)
        weights[code] += add
        remaining -= add
        if remaining == ZERO:
            return
    raise AllocationConstraintError("Class minimum cannot fit within asset caps")


def _reduce_members(
    weights: dict[str, Decimal],
    members: Sequence[str],
    *,
    amount: Decimal,
    minimums: Mapping[str, Decimal],
    rank_scores: Mapping[str, Decimal],
) -> None:
    remaining = amount
    for code in sorted(members, key=lambda item: (rank_scores.get(item, ZERO), item)):
        reducible = weights[code] - minimums.get(code, ZERO)
        if reducible <= ZERO:
            continue
        take = min(reducible, remaining)
        weights[code] -= take
        remaining -= take
        if remaining == ZERO:
            return
    raise AllocationConstraintError("Class cap conflicts with required minimums")


def _capacity(
    code: str,
    weights: Mapping[str, Decimal],
    asset_classes: Mapping[str, str],
    constraints: AllocationConstraints,
) -> Decimal:
    capacity = ONE - weights.get(code, ZERO)
    if constraints.max_single_asset_weight is not None:
        capacity = min(
            capacity,
            constraints.max_single_asset_weight - weights.get(code, ZERO),
        )
    class_code = asset_classes.get(code, code)
    class_caps = constraints.max_asset_class_weights or {}
    if class_code in class_caps:
        capacity = min(
            capacity,
            class_caps[class_code] - _class_weight(weights, asset_classes, class_code),
        )
    return max(capacity, ZERO)


def _class_members(
    weights: Mapping[str, Decimal],
    asset_classes: Mapping[str, str],
    class_code: str,
) -> list[str]:
    return [
        code for code in weights
        if asset_classes.get(code, code) == class_code
    ]


def _class_weight(
    weights: Mapping[str, Decimal],
    asset_classes: Mapping[str, str],
    class_code: str,
) -> Decimal:
    return sum(
        (
            weight for code, weight in weights.items()
            if asset_classes.get(code, code) == class_code
        ),
        ZERO,
    )


def _quantized_total(weights: Mapping[str, Decimal]) -> dict[str, Decimal]:
    positive = {code: weight for code, weight in weights.items() if weight > ZERO}
    if not positive:
        raise AllocationConstraintError("Final allocation has no positive weights")
    ordered = sorted(positive)
    final: dict[str, Decimal] = {}
    allocated = ZERO
    for code in ordered[:-1]:
        value = positive[code].quantize(WEIGHT_PRECISION, rounding=ROUND_DOWN)
        final[code] = value
        allocated += value
    final[ordered[-1]] = (ONE - allocated).quantize(WEIGHT_PRECISION)
    return final


def _validate_final(
    weights: Mapping[str, Decimal],
    asset_classes: Mapping[str, str],
    constraints: AllocationConstraints,
    minimums: Mapping[str, Decimal],
) -> None:
    total = sum(weights.values(), ZERO)
    if total != ONE:
        raise AllocationConstraintError("Final target allocation must sum to 100%")
    for code, floor in minimums.items():
        if weights.get(code, ZERO) < floor:
            raise AllocationConstraintError(f"{code} falls below required minimum")
    if constraints.max_single_asset_weight is not None:
        for code, weight in weights.items():
            if weight > constraints.max_single_asset_weight:
                raise AllocationConstraintError(f"{code} exceeds max single weight")
    for class_code, floor in (constraints.min_asset_class_weights or {}).items():
        if _class_weight(weights, asset_classes, class_code) < floor:
            raise AllocationConstraintError(f"{class_code} falls below class minimum")
    for class_code, cap in (constraints.max_asset_class_weights or {}).items():
        if _class_weight(weights, asset_classes, class_code) > cap:
            raise AllocationConstraintError(f"{class_code} exceeds class maximum")


def _soft_constraint_warnings(
    weights: Mapping[str, Decimal],
    asset_classes: Mapping[str, str],
    constraints: AllocationConstraints,
) -> list[str]:
    warnings: list[str] = []
    if constraints.soft_max_single_asset_weight is not None:
        for code, weight in sorted(weights.items()):
            if weight > constraints.soft_max_single_asset_weight:
                warnings.append(
                    f"Soft max single asset weight breached by {code}: {weight}."
                )
    for class_code, cap in (constraints.soft_max_asset_class_weights or {}).items():
        class_weight = _class_weight(weights, asset_classes, class_code)
        if class_weight > cap:
            warnings.append(
                f"Soft max asset-class weight breached by {class_code}: "
                f"{class_weight}."
            )
    return warnings


def _explain(
    adjustments: Sequence[ConstraintAdjustment],
    warnings: Sequence[str],
) -> str:
    if not adjustments and not warnings:
        return "Proposed weights already satisfy all configured constraints."
    parts: list[str] = []
    if adjustments:
        parts.append(
            "Hard constraints were applied and excess or missing weight was "
            "redistributed to eligible assets or cash."
        )
    if warnings:
        parts.append(
            "Soft constraints were left in place with warnings for review."
        )
    return " ".join(parts)
