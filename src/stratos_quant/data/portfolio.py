from __future__ import annotations

from collections import deque
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from stratos_quant.db import create_sqlite_engine

from .errors import DataExtractionError, MissingMarketDataError
from .models import HoldingValuation, PortfolioValuation


ZERO = Decimal("0")
ONE = Decimal("1")


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


class PortfolioValuationService:
    """Reconstruct portfolio holdings and cash from the transaction ledger."""

    def __init__(self, engine: Engine | None = None) -> None:
        self._engine = engine or create_sqlite_engine()

    def value_portfolio(
        self,
        portfolio_id: int,
        *,
        as_of: date | None = None,
        strict: bool = True,
    ) -> PortfolioValuation:
        """Return a portfolio valuation in its account currency.

        Prices and FX rates are selected on or before ``as_of``. When ``strict``
        is true, an unpriced holding or unavailable FX route raises a clean
        ``MissingMarketDataError`` instead of silently understating the total.
        """
        with self._engine.connect() as connection:
            portfolio = connection.execute(
                text(
                    """
                    SELECT p.id, p.name, a.currency_code
                    FROM portfolios p
                    JOIN accounts a ON a.id = p.account_id
                    WHERE p.id = :portfolio_id
                    """
                ),
                {"portfolio_id": portfolio_id},
            ).mappings().one_or_none()
            if portfolio is None:
                raise DataExtractionError(f"Portfolio does not exist: {portfolio_id}")

            holding_rows = connection.execute(
                text(
                    """
                    WITH net_holdings AS (
                        SELECT
                            portfolio_id,
                            security_id,
                            SUM(
                                CASE
                                    WHEN UPPER(type) = 'BUY' THEN quantity
                                    WHEN UPPER(type) = 'SELL' THEN -quantity
                                    ELSE 0
                                END
                            ) AS net_quantity
                        FROM transactions
                        WHERE portfolio_id = :portfolio_id
                          AND security_id IS NOT NULL
                          AND (:as_of IS NULL OR DATE(date) <= :as_of)
                        GROUP BY portfolio_id, security_id
                    ),
                    latest_prices AS (
                        SELECT security_id, date, close
                        FROM (
                            SELECT
                                security_id,
                                date,
                                close,
                                ROW_NUMBER() OVER (
                                    PARTITION BY security_id
                                    ORDER BY date DESC
                                ) AS row_number
                            FROM price_history
                            WHERE :as_of IS NULL OR date <= :as_of
                        )
                        WHERE row_number = 1
                    )
                    SELECT
                        s.id AS security_id,
                        s.ticker,
                        s.name,
                        s.asset_class AS asset_class_code,
                        s.currency_code AS security_currency,
                        nh.net_quantity,
                        lp.date AS price_date,
                        lp.close AS latest_close
                    FROM net_holdings nh
                    JOIN securities s ON s.id = nh.security_id
                    LEFT JOIN latest_prices lp ON lp.security_id = nh.security_id
                    WHERE nh.net_quantity != 0
                    ORDER BY s.ticker
                    """
                ),
                {
                    "portfolio_id": portfolio_id,
                    "as_of": as_of.isoformat() if as_of else None,
                },
            ).mappings().all()

            fx_graph = self._load_fx_graph(connection, as_of)
            cash_balance = self._calculate_cash(connection, portfolio_id, as_of)

        portfolio_currency = str(portfolio["currency_code"]).upper()
        holdings: list[HoldingValuation] = []
        missing: list[str] = []

        for row in holding_rows:
            ticker = str(row["ticker"])
            close = (
                _decimal(row["latest_close"])
                if row["latest_close"] is not None
                else None
            )
            fx_rate = self._find_fx_rate(
                fx_graph,
                str(row["security_currency"]).upper(),
                portfolio_currency,
            )
            market_value = None
            if close is None:
                missing.append(f"{ticker} (price)")
            elif fx_rate is None:
                missing.append(
                    f"{ticker} ({row['security_currency']}→{portfolio_currency} FX)"
                )
            else:
                market_value = _decimal(row["net_quantity"]) * close * fx_rate

            holdings.append(
                HoldingValuation(
                    security_id=int(row["security_id"]),
                    ticker=ticker,
                    name=str(row["name"]),
                    asset_class_code=str(row["asset_class_code"]),
                    security_currency=str(row["security_currency"]).upper(),
                    quantity=_decimal(row["net_quantity"]),
                    price_date=(
                        date.fromisoformat(str(row["price_date"]))
                        if row["price_date"] is not None
                        else None
                    ),
                    latest_close=close,
                    fx_rate=fx_rate,
                    market_value=market_value,
                )
            )

        if strict and missing:
            details = ", ".join(missing)
            raise MissingMarketDataError(
                f"Cannot fully value portfolio {portfolio_id}; missing market data: "
                f"{details}"
            )

        holdings_value = sum(
            (
                holding.market_value
                for holding in holdings
                if holding.market_value is not None
            ),
            ZERO,
        )
        return PortfolioValuation(
            portfolio_id=int(portfolio["id"]),
            portfolio_name=str(portfolio["name"]),
            currency=portfolio_currency,
            cash_balance=cash_balance,
            holdings_value=holdings_value,
            total_value=cash_balance + holdings_value,
            holdings=tuple(holdings),
        )

    @staticmethod
    def _calculate_cash(connection, portfolio_id: int, as_of: date | None) -> Decimal:
        rows = connection.execute(
            text(
                """
                SELECT type, total_value, fees, currency_exchange_rate
                FROM transactions
                WHERE portfolio_id = :portfolio_id
                  AND (:as_of IS NULL OR DATE(date) <= :as_of)
                """
            ),
            {
                "portfolio_id": portfolio_id,
                "as_of": as_of.isoformat() if as_of else None,
            },
        ).mappings()

        cash = ZERO
        for row in rows:
            transaction_type = str(row["type"]).upper()
            exchange_rate = _decimal(row["currency_exchange_rate"])
            gross_value = abs(_decimal(row["total_value"])) * exchange_rate
            fees = abs(_decimal(row["fees"])) * exchange_rate

            if transaction_type == "DEPOSIT":
                cash += gross_value - fees
            elif transaction_type == "WITHDRAWAL":
                cash -= gross_value + fees
            elif transaction_type == "BUY":
                cash -= gross_value + fees
            elif transaction_type == "SELL":
                cash += gross_value - fees
        return cash

    @staticmethod
    def _load_fx_graph(connection, as_of: date | None) -> dict[str, dict[str, Decimal]]:
        rows = connection.execute(
            text(
                """
                SELECT base_currency_code, quote_currency_code, close
                FROM (
                    SELECT
                        base_currency_code,
                        quote_currency_code,
                        close,
                        ROW_NUMBER() OVER (
                            PARTITION BY base_currency_code, quote_currency_code
                            ORDER BY date DESC
                        ) AS row_number
                    FROM fx_rate_history
                    WHERE :as_of IS NULL OR date <= :as_of
                )
                WHERE row_number = 1
                """
            ),
            {"as_of": as_of.isoformat() if as_of else None},
        ).mappings()

        graph: dict[str, dict[str, Decimal]] = {}
        for row in rows:
            base = str(row["base_currency_code"]).upper()
            quote = str(row["quote_currency_code"]).upper()
            rate = _decimal(row["close"])
            if rate <= ZERO:
                continue
            graph.setdefault(base, {})[quote] = rate
            graph.setdefault(quote, {})[base] = ONE / rate
        return graph

    @staticmethod
    def _find_fx_rate(
        graph: dict[str, dict[str, Decimal]],
        source_currency: str,
        target_currency: str,
    ) -> Decimal | None:
        if source_currency == target_currency:
            return ONE

        queue: deque[tuple[str, Decimal]] = deque([(source_currency, ONE)])
        visited = {source_currency}
        while queue:
            currency, accumulated_rate = queue.popleft()
            for next_currency, edge_rate in graph.get(currency, {}).items():
                if next_currency in visited:
                    continue
                next_rate = accumulated_rate * edge_rate
                if next_currency == target_currency:
                    return next_rate
                visited.add(next_currency)
                queue.append((next_currency, next_rate))
        return None
