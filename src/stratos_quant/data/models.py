from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class HoldingValuation:
    security_id: int
    ticker: str
    name: str
    asset_class_code: str
    security_currency: str
    quantity: Decimal
    price_date: date | None
    latest_close: Decimal | None
    fx_rate: Decimal | None
    market_value: Decimal | None

    @property
    def is_priced(self) -> bool:
        return self.market_value is not None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["is_priced"] = self.is_priced
        return result


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    portfolio_id: int
    portfolio_name: str
    currency: str
    cash_balance: Decimal
    holdings_value: Decimal
    total_value: Decimal
    holdings: tuple[HoldingValuation, ...]

    @property
    def is_complete(self) -> bool:
        return all(holding.is_priced for holding in self.holdings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id,
            "portfolio_name": self.portfolio_name,
            "currency": self.currency,
            "cash_balance": self.cash_balance,
            "holdings_value": self.holdings_value,
            "total_value": self.total_value,
            "is_complete": self.is_complete,
            "holdings": [holding.to_dict() for holding in self.holdings],
        }
