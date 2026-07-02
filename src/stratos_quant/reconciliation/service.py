from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import sqlite3
from typing import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Engine

from stratos_quant.data import PortfolioValuationService
from stratos_quant.db import ensure_strategy_schema

from .errors import ReconciliationError
from .models import (
    AssetClassDrift,
    DriftBand,
    RebalanceMandate,
    RebalanceScheduleDecision,
    ReconciliationResult,
)


ZERO = Decimal("0")
ONE = Decimal("1")
MONEY = Decimal("0.01")
WEIGHT = Decimal("0.0000000001")
QUANTITY = Decimal("0.0000000001")

sqlite3.register_adapter(Decimal, str)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


class ReconciliationService:
    """Convert asset-class target drift into persisted security trade mandates."""

    def __init__(
        self,
        engine: Engine,
        *,
        valuation_service: PortfolioValuationService | None = None,
    ) -> None:
        self._engine = engine
        self._valuation_service = valuation_service or PortfolioValuationService(
            engine
        )
        ensure_strategy_schema(engine)

    def reconcile(
        self,
        *,
        run_id: int,
        portfolio_id: int,
        drift_threshold: Decimal = Decimal("0.01"),
        drift_bands: Mapping[str, DriftBand | tuple[Decimal, Decimal]] | None = None,
        minimum_trade_value: Decimal = Decimal("0"),
        fixed_trade_fee: Decimal = Decimal("0"),
        broker_fee_rate: Decimal = Decimal("0"),
        slippage_rate: Decimal = Decimal("0"),
        tax_rate: Decimal = Decimal("0"),
        expected_benefit_rate: Decimal = Decimal("1"),
        max_cost_ratio: Decimal | None = Decimal("1"),
        fractional_shares: bool = True,
        schedule: str = "MANUAL",
        last_rebalance_date: date | None = None,
        regime_changed: bool = False,
        as_of: date | None = None,
        asset_class_map: Mapping[str, str] | None = None,
        persist: bool = True,
    ) -> ReconciliationResult:
        """Calculate and optionally persist BUY/SELL mandates.

        Drift is suppressed when its absolute portfolio weight is strictly below
        ``drift_threshold``. Trade values are positive magnitudes in portfolio
        currency; direction is carried by ``action_type``. Epic 5 drift bands
        are absolute percentage-point offsets around each target weight.
        """
        threshold = _decimal(drift_threshold)
        if threshold < ZERO or threshold >= ONE:
            raise ValueError("drift_threshold must be between 0 and 1")
        minimum_trade_value = _decimal(minimum_trade_value)
        fixed_trade_fee = _decimal(fixed_trade_fee)
        broker_fee_rate = _decimal(broker_fee_rate)
        slippage_rate = _decimal(slippage_rate)
        tax_rate = _decimal(tax_rate)
        expected_benefit_rate = _decimal(expected_benefit_rate)
        if max_cost_ratio is not None:
            max_cost_ratio = _decimal(max_cost_ratio)
        if minimum_trade_value < ZERO:
            raise ValueError("minimum_trade_value must be non-negative")
        for name, value in {
            "fixed_trade_fee": fixed_trade_fee,
            "broker_fee_rate": broker_fee_rate,
            "slippage_rate": slippage_rate,
            "tax_rate": tax_rate,
            "expected_benefit_rate": expected_benefit_rate,
        }.items():
            if value < ZERO:
                raise ValueError(f"{name} must be non-negative")

        valuation = self._valuation_service.value_portfolio(
            portfolio_id,
            as_of=as_of,
            strict=True,
        )
        if valuation.total_value <= ZERO:
            raise ReconciliationError(
                "Portfolio total value must be positive for reconciliation"
            )

        targets, recommendations = self._load_run_inputs(run_id, portfolio_id)
        normalized_map = {
            ticker.upper(): code.strip().upper()
            for ticker, code in (asset_class_map or {}).items()
        }

        holdings_by_class: dict[str, list] = defaultdict(list)
        for holding in valuation.holdings:
            code = normalized_map.get(
                holding.ticker.upper(),
                holding.asset_class_code.upper(),
            )
            holdings_by_class[code].append(holding)

        recommendations_by_class: dict[str, list[dict[str, object]]] = defaultdict(list)
        for recommendation in recommendations:
            code = normalized_map.get(
                str(recommendation["ticker"]).upper(),
                str(recommendation["database_asset_class"]).upper(),
            )
            recommendation["resolved_asset_class"] = code
            recommendations_by_class[code].append(recommendation)

        current_values = {
            code: sum(
                (holding.market_value or ZERO for holding in holdings),
                ZERO,
            )
            for code, holdings in holdings_by_class.items()
        }
        current_values["CASH"] = valuation.cash_balance

        normalized_bands = _normalize_drift_bands(drift_bands)
        schedule_decision = self.schedule_decision(
            schedule=schedule,
            as_of=as_of or date.today(),
            last_rebalance_date=last_rebalance_date,
            drift_breach=False,
            regime_changed=regime_changed,
        )

        all_classes = sorted(set(current_values) | set(targets))
        drifts: list[AssetClassDrift] = []
        mandates: list[RebalanceMandate] = []
        skipped_mandates: list[RebalanceMandate] = []
        for code in all_classes:
            current_value = current_values.get(code, ZERO)
            target_weight = targets.get(code, ZERO)
            target_value = valuation.total_value * target_weight
            drift_value = target_value - current_value
            drift_weight = drift_value / valuation.total_value
            current_weight = (current_value / valuation.total_value).quantize(WEIGHT)
            band = normalized_bands.get(code, DriftBand(-threshold, threshold))
            allowed_min = max(ZERO, target_weight + band.min_drift).quantize(WEIGHT)
            allowed_max = min(ONE, target_weight + band.max_drift).quantize(WEIGHT)
            allocation_drift_weight = (current_weight - target_weight).quantize(
                WEIGHT
            )
            suppressed = allowed_min <= current_weight <= allowed_max
            rebalance_required = not suppressed
            drifts.append(
                AssetClassDrift(
                    asset_class_code=code,
                    current_value=current_value,
                    current_weight=current_weight,
                    target_value=target_value,
                    target_weight=target_weight,
                    drift_value=drift_value,
                    drift_weight=drift_weight.quantize(WEIGHT),
                    suppressed=suppressed,
                    allowed_min=allowed_min,
                    allowed_max=allowed_max,
                    allocation_drift_weight=allocation_drift_weight,
                    rebalance_weight=drift_weight.quantize(WEIGHT),
                    rebalance_required=rebalance_required,
                )
            )
            if suppressed or code == "CASH":
                continue
            class_mandates, class_skipped = self._build_class_mandates(
                asset_class_code=code,
                portfolio_value=valuation.total_value,
                target_weight=target_weight,
                allowed_min=allowed_min,
                allowed_max=allowed_max,
                holdings=holdings_by_class.get(code, []),
                recommendations=recommendations_by_class.get(code, []),
                minimum_trade_value=minimum_trade_value,
                fixed_trade_fee=fixed_trade_fee,
                broker_fee_rate=broker_fee_rate,
                slippage_rate=slippage_rate,
                tax_rate=tax_rate,
                expected_benefit_rate=expected_benefit_rate,
                max_cost_ratio=max_cost_ratio,
                fractional_shares=fractional_shares,
            )
            mandates.extend(class_mandates)
            skipped_mandates.extend(class_skipped)

        portfolio_drift = (
            sum((abs(drift.allocation_drift_weight or ZERO) for drift in drifts), ZERO)
            / Decimal("2")
        ).quantize(WEIGHT)
        drift_breach = any(drift.rebalance_required for drift in drifts)
        trigger_reasons = list(schedule_decision.trigger_reasons)
        if drift_breach and "DRIFT_BREACH" not in trigger_reasons:
            trigger_reasons.append("DRIFT_BREACH")
        if regime_changed and "REGIME_CHANGE" not in trigger_reasons:
            trigger_reasons.append("REGIME_CHANGE")

        mandates, cash_skipped = self._enforce_cash_limit(
            mandates,
            valuation.cash_balance,
        )
        skipped_mandates.extend(cash_skipped)

        expected_benefit = _sum_attr(mandates, "expected_benefit")
        estimated_fees = _sum_attr(mandates, "estimated_fees")
        estimated_slippage = _sum_attr(mandates, "estimated_slippage")
        estimated_tax_cost = _sum_attr(mandates, "estimated_tax_cost")
        net_expected_benefit = _sum_attr(mandates, "net_expected_benefit")
        result = ReconciliationResult(
            run_id=run_id,
            portfolio_id=portfolio_id,
            currency=valuation.currency,
            portfolio_value=valuation.total_value,
            drift_threshold=threshold,
            drifts=tuple(drifts),
            mandates=tuple(mandates),
            skipped_mandates=tuple(skipped_mandates),
            portfolio_drift=portfolio_drift,
            rebalance_required=bool(mandates),
            trigger_reasons=tuple(trigger_reasons),
            schedule=schedule.upper(),
            expected_benefit=expected_benefit,
            estimated_fees=estimated_fees,
            estimated_slippage=estimated_slippage,
            estimated_tax_cost=estimated_tax_cost,
            net_expected_benefit=net_expected_benefit,
            explanation=_rebalance_explanation(
                mandates=mandates,
                skipped_mandates=skipped_mandates,
                drift_breach=drift_breach,
                trigger_reasons=trigger_reasons,
                net_expected_benefit=net_expected_benefit,
            ),
        )
        if persist:
            self._persist_mandates(result)
        return result

    @staticmethod
    def schedule_decision(
        *,
        schedule: str,
        as_of: date,
        last_rebalance_date: date | None = None,
        drift_breach: bool = False,
        regime_changed: bool = False,
    ) -> RebalanceScheduleDecision:
        normalized = schedule.strip().upper()
        if normalized not in {"MANUAL", "WEEKLY", "MONTHLY"}:
            raise ValueError("schedule must be MANUAL, WEEKLY, or MONTHLY")

        reasons: list[str] = []
        scheduled_due = False
        if normalized == "WEEKLY":
            scheduled_due = (
                last_rebalance_date is None
                or as_of - last_rebalance_date >= timedelta(days=7)
            )
        elif normalized == "MONTHLY":
            scheduled_due = (
                last_rebalance_date is None
                or (as_of.year, as_of.month)
                != (last_rebalance_date.year, last_rebalance_date.month)
            )
        if scheduled_due:
            reasons.append(f"{normalized}_SCHEDULE")
        if drift_breach:
            reasons.append("DRIFT_BREACH")
        if regime_changed:
            reasons.append("REGIME_CHANGE")
        return RebalanceScheduleDecision(
            schedule=normalized,
            as_of=as_of,
            due=bool(reasons),
            trigger_reasons=tuple(reasons),
        )

    def _load_run_inputs(
        self,
        run_id: int,
        portfolio_id: int,
    ) -> tuple[dict[str, Decimal], list[dict[str, object]]]:
        with self._engine.connect() as connection:
            run_exists = connection.execute(
                text("SELECT 1 FROM strategy_runs WHERE id = :run_id"),
                {"run_id": run_id},
            ).scalar_one_or_none()
            if run_exists is None:
                raise ReconciliationError(f"Strategy run does not exist: {run_id}")

            target_rows = connection.execute(
                text(
                    """
                    SELECT asset_class_code, target_weight
                    FROM strategy_target_allocations
                    WHERE run_id = :run_id AND portfolio_id = :portfolio_id
                    """
                ),
                {"run_id": run_id, "portfolio_id": portfolio_id},
            ).mappings().all()
            if not target_rows:
                raise ReconciliationError(
                    f"No targets found for run {run_id}, portfolio {portfolio_id}"
                )
            targets = {
                str(row["asset_class_code"]).upper(): _decimal(row["target_weight"])
                for row in target_rows
            }
            if sum(targets.values(), ZERO) != ONE:
                raise ReconciliationError(
                    "Strategy target weights must sum to exactly 1"
                )

            recommendation_rows = connection.execute(
                text(
                    """
                    SELECT
                        ar.id,
                        ar.security_id,
                        ar.action_type,
                        ar.target_weight,
                        ar.llm_security_rationale,
                        s.ticker,
                        s.asset_class AS database_asset_class
                    FROM asset_recommendations ar
                    JOIN securities s ON s.id = ar.security_id
                    WHERE ar.run_id = :run_id
                      AND ar.portfolio_id = :portfolio_id
                      AND ar.is_executed = 0
                    ORDER BY ar.id
                    """
                ),
                {"run_id": run_id, "portfolio_id": portfolio_id},
            ).mappings().all()
        return targets, [dict(row) for row in recommendation_rows]

    def _build_class_mandates(
        self,
        *,
        asset_class_code: str,
        portfolio_value: Decimal,
        target_weight: Decimal,
        allowed_min: Decimal,
        allowed_max: Decimal,
        holdings: list,
        recommendations: list[dict[str, object]],
        minimum_trade_value: Decimal,
        fixed_trade_fee: Decimal,
        broker_fee_rate: Decimal,
        slippage_rate: Decimal,
        tax_rate: Decimal,
        expected_benefit_rate: Decimal,
        max_cost_ratio: Decimal | None,
        fractional_shares: bool,
    ) -> tuple[list[RebalanceMandate], list[RebalanceMandate]]:
        current_by_security = {
            holding.security_id: holding.market_value or ZERO
            for holding in holdings
        }
        holding_by_security = {
            holding.security_id: holding for holding in holdings
        }
        recommendation_by_security = {
            int(recommendation["security_id"]): recommendation
            for recommendation in recommendations
        }

        desired_weights: dict[int, Decimal] = {}
        if target_weight > ZERO:
            eligible = [
                recommendation
                for recommendation in recommendations
                if _decimal(recommendation["target_weight"]) > ZERO
            ]
            if not eligible:
                raise ReconciliationError(
                    f"No LLM-selected securities available for positive target "
                    f"{asset_class_code}"
                )
            recommendation_total = sum(
                (_decimal(item["target_weight"]) for item in eligible),
                ZERO,
            )
            if recommendation_total != target_weight:
                raise ReconciliationError(
                    f"Recommendation weights for {asset_class_code} "
                    f"({recommendation_total}) do not equal target ({target_weight})"
                )
            desired_weights = {
                int(item["security_id"]): _decimal(item["target_weight"])
                for item in eligible
            }

        security_ids = sorted(set(current_by_security) | set(desired_weights))
        mandates: list[RebalanceMandate] = []
        skipped: list[RebalanceMandate] = []
        for security_id in security_ids:
            current_value = current_by_security.get(security_id, ZERO)
            security_target_weight = desired_weights.get(security_id, ZERO)
            desired_value = portfolio_value * security_target_weight
            trade_value = (desired_value - current_value).quantize(
                MONEY,
                rounding=ROUND_HALF_UP,
            )
            if trade_value == ZERO:
                continue

            recommendation = recommendation_by_security.get(security_id)
            holding = holding_by_security.get(security_id)
            ticker = (
                str(recommendation["ticker"])
                if recommendation is not None
                else str(holding.ticker)
            )
            action = "BUY" if trade_value > ZERO else "SELL"
            rationale = (
                str(recommendation["llm_security_rationale"])
                if recommendation is not None
                else (
                    f"Reduce {ticker} because it is not selected in the "
                    f"{asset_class_code} target portfolio."
                )
            )
            mandate = self._build_mandate(
                security_id=security_id,
                ticker=ticker,
                asset_class_code=asset_class_code,
                action=action,
                target_weight=security_target_weight,
                trade_value=abs(trade_value),
                current_value=current_value,
                target_value=desired_value,
                allowed_min=allowed_min,
                allowed_max=allowed_max,
                holding=holding,
                rationale=rationale,
                recommendation_id=(
                    int(recommendation["id"])
                    if recommendation is not None
                    else None
                ),
                portfolio_value=portfolio_value,
                minimum_trade_value=minimum_trade_value,
                fixed_trade_fee=fixed_trade_fee,
                broker_fee_rate=broker_fee_rate,
                slippage_rate=slippage_rate,
                tax_rate=tax_rate,
                expected_benefit_rate=expected_benefit_rate,
                max_cost_ratio=max_cost_ratio,
                fractional_shares=fractional_shares,
            )
            if mandate.skipped_reason is None:
                mandates.append(mandate)
            else:
                skipped.append(mandate)
        return mandates, skipped

    def _build_mandate(
        self,
        *,
        security_id: int,
        ticker: str,
        asset_class_code: str,
        action: str,
        target_weight: Decimal,
        trade_value: Decimal,
        current_value: Decimal,
        target_value: Decimal,
        allowed_min: Decimal,
        allowed_max: Decimal,
        holding,
        rationale: str,
        recommendation_id: int | None,
        portfolio_value: Decimal,
        minimum_trade_value: Decimal,
        fixed_trade_fee: Decimal,
        broker_fee_rate: Decimal,
        slippage_rate: Decimal,
        tax_rate: Decimal,
        expected_benefit_rate: Decimal,
        max_cost_ratio: Decimal | None,
        fractional_shares: bool,
    ) -> RebalanceMandate:
        executable_value = trade_value
        estimated_quantity = None
        unit_value = _holding_unit_value(holding)
        if unit_value is not None and unit_value > ZERO:
            quantity = (trade_value / unit_value).quantize(QUANTITY)
            if not fractional_shares:
                quantity = quantity.to_integral_value(rounding=ROUND_DOWN)
                executable_value = (quantity * unit_value).quantize(
                    MONEY,
                    rounding=ROUND_HALF_UP,
                )
            estimated_quantity = quantity

        skipped_reason = None
        if executable_value < minimum_trade_value:
            skipped_reason = "Trade value is below the configured minimum."

        estimated_fees = (fixed_trade_fee + executable_value * broker_fee_rate).quantize(
            MONEY,
            rounding=ROUND_HALF_UP,
        )
        estimated_slippage = (executable_value * slippage_rate).quantize(
            MONEY,
            rounding=ROUND_HALF_UP,
        )
        estimated_tax_cost = (
            executable_value * tax_rate if action == "SELL" else ZERO
        ).quantize(MONEY, rounding=ROUND_HALF_UP)
        expected_benefit = (executable_value * expected_benefit_rate).quantize(
            MONEY,
            rounding=ROUND_HALF_UP,
        )
        total_cost = estimated_fees + estimated_slippage + estimated_tax_cost
        net_expected_benefit = expected_benefit - total_cost
        if skipped_reason is None and net_expected_benefit <= ZERO:
            skipped_reason = "Estimated cost is greater than or equal to benefit."
        if (
            skipped_reason is None
            and max_cost_ratio is not None
            and expected_benefit > ZERO
            and total_cost > expected_benefit * max_cost_ratio
        ):
            skipped_reason = "Estimated cost is too high relative to benefit."

        return RebalanceMandate(
            security_id=security_id,
            ticker=ticker,
            asset_class_code=asset_class_code,
            action_type=action,
            target_weight=target_weight,
            estimated_trade_value=executable_value,
            rationale=rationale,
            recommendation_id=recommendation_id,
            current_weight=(current_value / portfolio_value).quantize(WEIGHT),
            allowed_min=allowed_min,
            allowed_max=allowed_max,
            rebalance_weight=((target_value - current_value) / portfolio_value).quantize(
                WEIGHT
            ),
            current_value=current_value,
            target_value=target_value,
            estimated_quantity=estimated_quantity,
            estimated_fees=estimated_fees,
            estimated_slippage=estimated_slippage,
            estimated_tax_cost=estimated_tax_cost,
            expected_benefit=expected_benefit,
            net_expected_benefit=net_expected_benefit,
            skipped_reason=skipped_reason,
        )

    @staticmethod
    def _enforce_cash_limit(
        mandates: list[RebalanceMandate],
        cash_balance: Decimal,
    ) -> tuple[list[RebalanceMandate], list[RebalanceMandate]]:
        sell_inflow = sum(
            (
                mandate.estimated_trade_value
                - mandate.estimated_fees
                - mandate.estimated_slippage
                - mandate.estimated_tax_cost
                for mandate in mandates
                if mandate.action_type == "SELL"
            ),
            ZERO,
        )
        buy_budget = cash_balance + sell_inflow
        buy_outflow = sum(
            (
                mandate.estimated_trade_value
                + mandate.estimated_fees
                + mandate.estimated_slippage
                for mandate in mandates
                if mandate.action_type == "BUY"
            ),
            ZERO,
        )
        if buy_outflow <= buy_budget:
            return mandates, []

        remaining_budget = max(buy_budget, ZERO)
        kept: list[RebalanceMandate] = []
        skipped: list[RebalanceMandate] = []
        for mandate in mandates:
            if mandate.action_type != "BUY":
                kept.append(mandate)
                continue
            required_cash = (
                mandate.estimated_trade_value
                + mandate.estimated_fees
                + mandate.estimated_slippage
            )
            if required_cash <= remaining_budget:
                kept.append(mandate)
                remaining_budget -= required_cash
                continue
            skipped.append(
                _copy_mandate_with_skip(
                    mandate,
                    "Buy order would exceed available cash.",
                )
            )
        return kept, skipped

    def _persist_mandates(self, result: ReconciliationResult) -> None:
        with self._engine.begin() as connection:
            rebalance_run_id = connection.execute(
                text(
                    """
                    INSERT INTO rebalance_runs
                        (strategy_run_id, portfolio_id, schedule, trigger_reasons,
                         portfolio_value, portfolio_drift, rebalance_required,
                         expected_benefit, estimated_fees, estimated_slippage,
                         estimated_tax_cost, net_expected_benefit, explanation)
                    VALUES
                        (:run_id, :portfolio_id, :schedule, :trigger_reasons,
                         :portfolio_value, :portfolio_drift, :rebalance_required,
                         :expected_benefit, :estimated_fees, :estimated_slippage,
                         :estimated_tax_cost, :net_expected_benefit, :explanation)
                    """
                ),
                {
                    "run_id": result.run_id,
                    "portfolio_id": result.portfolio_id,
                    "schedule": result.schedule,
                    "trigger_reasons": ",".join(result.trigger_reasons),
                    "portfolio_value": result.portfolio_value,
                    "portfolio_drift": result.portfolio_drift,
                    "rebalance_required": result.rebalance_required,
                    "expected_benefit": result.expected_benefit,
                    "estimated_fees": result.estimated_fees,
                    "estimated_slippage": result.estimated_slippage,
                    "estimated_tax_cost": result.estimated_tax_cost,
                    "net_expected_benefit": result.net_expected_benefit,
                    "explanation": result.explanation,
                },
            ).lastrowid
            for mandate in (*result.mandates, *result.skipped_mandates):
                connection.execute(
                    text(
                        """
                        INSERT INTO rebalance_trade_proposals
                            (rebalance_run_id, strategy_run_id, portfolio_id,
                             security_id, ticker, asset_class_code, side,
                             current_weight, target_weight, allowed_min,
                             allowed_max, rebalance_weight, current_value,
                             target_value, trade_value, estimated_quantity,
                             estimated_fees, estimated_slippage,
                             estimated_tax_cost, expected_benefit,
                             net_expected_benefit, skipped_reason, rationale)
                        VALUES
                            (:rebalance_run_id, :run_id, :portfolio_id,
                             :security_id, :ticker, :asset_class_code, :side,
                             :current_weight, :target_weight, :allowed_min,
                             :allowed_max, :rebalance_weight, :current_value,
                             :target_value, :trade_value, :estimated_quantity,
                             :estimated_fees, :estimated_slippage,
                             :estimated_tax_cost, :expected_benefit,
                             :net_expected_benefit, :skipped_reason, :rationale)
                        """
                    ),
                    {
                        "rebalance_run_id": rebalance_run_id,
                        "run_id": result.run_id,
                        "portfolio_id": result.portfolio_id,
                        "security_id": mandate.security_id,
                        "ticker": mandate.ticker,
                        "asset_class_code": mandate.asset_class_code,
                        "side": mandate.action_type,
                        "current_weight": mandate.current_weight,
                        "target_weight": mandate.target_weight,
                        "allowed_min": mandate.allowed_min,
                        "allowed_max": mandate.allowed_max,
                        "rebalance_weight": mandate.rebalance_weight,
                        "current_value": mandate.current_value,
                        "target_value": mandate.target_value,
                        "trade_value": mandate.estimated_trade_value,
                        "estimated_quantity": mandate.estimated_quantity,
                        "estimated_fees": mandate.estimated_fees,
                        "estimated_slippage": mandate.estimated_slippage,
                        "estimated_tax_cost": mandate.estimated_tax_cost,
                        "expected_benefit": mandate.expected_benefit,
                        "net_expected_benefit": mandate.net_expected_benefit,
                        "skipped_reason": mandate.skipped_reason,
                        "rationale": mandate.rationale,
                    },
                )
            for mandate in result.mandates:
                parameters = {
                    "run_id": result.run_id,
                    "portfolio_id": result.portfolio_id,
                    "security_id": mandate.security_id,
                    "action_type": mandate.action_type,
                    "target_weight": mandate.target_weight,
                    "estimated_trade_value": mandate.estimated_trade_value,
                    "rationale": mandate.rationale,
                }
                if mandate.recommendation_id is not None:
                    connection.execute(
                        text(
                            """
                            UPDATE asset_recommendations
                            SET action_type = :action_type,
                                target_weight = :target_weight,
                                estimated_trade_value = :estimated_trade_value,
                                recommendation_timestamp = CURRENT_TIMESTAMP
                            WHERE id = :recommendation_id
                              AND is_executed = 0
                            """
                        ),
                        {
                            **parameters,
                            "recommendation_id": mandate.recommendation_id,
                        },
                    )
                else:
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
                        parameters,
                    )


def _normalize_drift_bands(
    drift_bands: Mapping[str, DriftBand | tuple[Decimal, Decimal]] | None,
) -> dict[str, DriftBand]:
    normalized: dict[str, DriftBand] = {}
    for code, band in (drift_bands or {}).items():
        if isinstance(band, DriftBand):
            min_drift = _decimal(band.min_drift)
            max_drift = _decimal(band.max_drift)
        else:
            min_drift = _decimal(band[0])
            max_drift = _decimal(band[1])
        if min_drift > ZERO or max_drift < ZERO or min_drift > max_drift:
            raise ValueError(
                "drift band must be an asymmetric absolute range around target"
            )
        normalized[code.strip().upper()] = DriftBand(min_drift, max_drift)
    return normalized


def _holding_unit_value(holding) -> Decimal | None:
    if holding is None:
        return None
    latest_close = getattr(holding, "latest_close", None)
    if latest_close is None:
        return None
    fx_rate = getattr(holding, "fx_rate", ONE) or ONE
    return (_decimal(latest_close) * _decimal(fx_rate)).quantize(
        MONEY,
        rounding=ROUND_HALF_UP,
    )


def _sum_attr(mandates: list[RebalanceMandate], name: str) -> Decimal:
    return sum((getattr(mandate, name) for mandate in mandates), ZERO)


def _copy_mandate_with_skip(
    mandate: RebalanceMandate,
    skipped_reason: str,
) -> RebalanceMandate:
    return RebalanceMandate(
        security_id=mandate.security_id,
        ticker=mandate.ticker,
        asset_class_code=mandate.asset_class_code,
        action_type=mandate.action_type,
        target_weight=mandate.target_weight,
        estimated_trade_value=mandate.estimated_trade_value,
        rationale=mandate.rationale,
        recommendation_id=mandate.recommendation_id,
        current_weight=mandate.current_weight,
        allowed_min=mandate.allowed_min,
        allowed_max=mandate.allowed_max,
        rebalance_weight=mandate.rebalance_weight,
        current_value=mandate.current_value,
        target_value=mandate.target_value,
        estimated_quantity=mandate.estimated_quantity,
        estimated_fees=mandate.estimated_fees,
        estimated_slippage=mandate.estimated_slippage,
        estimated_tax_cost=mandate.estimated_tax_cost,
        expected_benefit=mandate.expected_benefit,
        net_expected_benefit=mandate.net_expected_benefit,
        skipped_reason=skipped_reason,
    )


def _rebalance_explanation(
    *,
    mandates: list[RebalanceMandate],
    skipped_mandates: list[RebalanceMandate],
    drift_breach: bool,
    trigger_reasons: list[str],
    net_expected_benefit: Decimal,
) -> str:
    if not drift_breach and not trigger_reasons:
        return "No rebalance needed: all assets are inside their drift bands."
    if mandates:
        return (
            f"Rebalance proposed for {len(mandates)} trade(s). "
            f"Net expected benefit after fees, slippage, and tax is "
            f"{net_expected_benefit}."
        )
    if skipped_mandates:
        return (
            "Rebalance was skipped because every proposed trade failed minimum "
            "trade, cash, or cost-benefit checks."
        )
    return "Rebalance trigger recorded, but no eligible trade was generated."
