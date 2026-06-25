from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

import pandas as pd
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from stratos_quant.db import create_sqlite_engine


class PriceHistoryLoader:
    """Load adjusted strategy inputs from the existing price history schema."""

    def __init__(self, engine: Engine | None = None) -> None:
        self._engine = engine or create_sqlite_engine()

    def load(
        self,
        *,
        security_ids: Sequence[int] | None = None,
        as_of: date | None = None,
        asset_class_map: Mapping[str, str] | None = None,
    ) -> pd.DataFrame:
        """Return long-form prices with strategy-level asset class codes."""
        sql = """
            SELECT
                ph.security_id,
                s.ticker,
                s.asset_class AS database_asset_class,
                ph.date,
                ph.close
            FROM price_history ph
            JOIN securities s ON s.id = ph.security_id
            WHERE ph.close > 0
              AND (:as_of IS NULL OR ph.date <= :as_of)
        """
        parameters: dict[str, object] = {
            "as_of": as_of.isoformat() if as_of else None,
        }
        if security_ids is not None:
            if not security_ids:
                return self._empty_frame()
            sql += " AND ph.security_id IN :security_ids"
            parameters["security_ids"] = list(security_ids)

        statement = text(sql + " ORDER BY ph.date, ph.security_id")
        if security_ids is not None:
            statement = statement.bindparams(bindparam("security_ids", expanding=True))

        with self._engine.connect() as connection:
            frame = pd.read_sql_query(statement, connection, params=parameters)

        if frame.empty:
            return self._empty_frame()

        normalized_map = {
            ticker.upper(): code.strip().upper()
            for ticker, code in (asset_class_map or {}).items()
        }
        frame["asset_class_code"] = [
            normalized_map.get(str(ticker).upper(), str(database_code).upper())
            for ticker, database_code in zip(
                frame["ticker"],
                frame["database_asset_class"],
                strict=True,
            )
        ]
        frame["date"] = pd.to_datetime(frame["date"])
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["close"])
        return frame[
            ["security_id", "ticker", "asset_class_code", "date", "close"]
        ].reset_index(drop=True)

    @staticmethod
    def _empty_frame() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "security_id",
                "ticker",
                "asset_class_code",
                "date",
                "close",
            ]
        )
