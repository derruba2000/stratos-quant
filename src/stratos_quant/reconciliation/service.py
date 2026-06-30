from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Engine

from stratos_quant.data import PortfolioValuationService
from stratos_quant.db import ensure_strategy_schema

from .errors import ReconciliationError
from .models import AssetClassDrift, RebalanceMandate, ReconciliationResult


ZERO = Decimal("0")
ONE = Decimal("1")
MONEY = Decimal("0.01")
WEIGHT = Decimal("0.0000000001")


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
        as_of: date | None = None,
        asset_class_map: Mapping[str, str] | None = None,
        persist: bool = True,
    ) -> ReconciliationResult:
        """Calculate and optionally persist BUY/SELL mandates.

        Drift is suppressed when its absolute portfolio weight is strictly below
        ``drift_threshold``. Trade values are positive magnitudes in portfolio
        currency; direction is carried by ``action_type``.
        """
        threshold = _decimal(drift_threshold)
        if threshold < ZERO or threshold >= ONE:
            raise ValueError("drift_threshold must be between 0 and 1")

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

        all_classes = sorted(set(current_values) | set(targets))
        drifts: list[AssetClassDrift] = []
        mandates: list[RebalanceMandate] = []
        for code in all_classes:
            current_value = current_values.get(code, ZERO)
            target_weight = targets.get(code, ZERO)
            target_value = valuation.total_value * target_weight
            drift_value = target_value - current_value
            drift_weight = drift_value / valuation.total_value
            suppressed = abs(drift_weight) < threshold
            drifts.append(
                AssetClassDrift(
                    asset_class_code=code,
                    current_value=current_value,
                    current_weight=(current_value / valuation.total_value).quantize(
                        WEIGHT
                    ),
                    target_value=target_value,
                    target_weight=target_weight,
                    drift_value=drift_value,
                    drift_weight=drift_weight.quantize(WEIGHT),
                    suppressed=suppressed,
                )
            )
            if suppressed or code == "CASH":
                continue
            mandates.extend(
                self._build_class_mandates(
                    asset_class_code=code,
                    portfolio_value=valuation.total_value,
                    target_weight=target_weight,
                    holdings=holdings_by_class.get(code, []),
                    recommendations=recommendations_by_class.get(code, []),
                )
            )

        result = ReconciliationResult(
            run_id=run_id,
            portfolio_id=portfolio_id,
            currency=valuation.currency,
            portfolio_value=valuation.total_value,
            drift_threshold=threshold,
            drifts=tuple(drifts),
            mandates=tuple(mandates),
        )
        if persist:
            self._persist_mandates(result)
        return result

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
        holdings: list,
        recommendations: list[dict[str, object]],
    ) -> list[RebalanceMandate]:
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
            mandates.append(
                RebalanceMandate(
                    security_id=security_id,
                    ticker=ticker,
                    asset_class_code=asset_class_code,
                    action_type=action,
                    target_weight=security_target_weight,
                    estimated_trade_value=abs(trade_value),
                    rationale=rationale,
                    recommendation_id=(
                        int(recommendation["id"])
                        if recommendation is not None
                        else None
                    ),
                )
            )
        return mandates

    def _persist_mandates(self, result: ReconciliationResult) -> None:
        with self._engine.begin() as connection:
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
