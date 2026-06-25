from __future__ import annotations

from datetime import date
from decimal import Decimal
import json

import pytest
from sqlalchemy import create_engine

from stratos_quant.data import (
    FundDataExtractor,
    MissingMarketDataError,
    PortfolioValuationService,
)


@pytest.fixture
def epic2_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'epic2.sqlite3'}")
    statements = [
        """
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY,
            currency_code VARCHAR(3) NOT NULL
        )
        """,
        """
        CREATE TABLE portfolios (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL,
            name VARCHAR(255) NOT NULL
        )
        """,
        """
        CREATE TABLE securities (
            id INTEGER PRIMARY KEY,
            ticker VARCHAR(32) NOT NULL,
            name VARCHAR(255) NOT NULL,
            asset_class VARCHAR(32) NOT NULL,
            currency_code VARCHAR(3) NOT NULL
        )
        """,
        """
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY,
            portfolio_id INTEGER NOT NULL,
            security_id INTEGER,
            date DATETIME NOT NULL,
            type VARCHAR(10) NOT NULL,
            quantity INTEGER NOT NULL,
            price DECIMAL(32, 10) NOT NULL,
            fees DECIMAL(32, 10) NOT NULL,
            total_value DECIMAL(32, 10) NOT NULL,
            currency_exchange_rate DECIMAL(32, 10) NOT NULL
        )
        """,
        """
        CREATE TABLE price_history (
            security_id INTEGER NOT NULL,
            date DATE NOT NULL,
            close NUMERIC(32, 10) NOT NULL,
            PRIMARY KEY (security_id, date)
        )
        """,
        """
        CREATE TABLE fx_rate_history (
            base_currency_code VARCHAR(3) NOT NULL,
            quote_currency_code VARCHAR(3) NOT NULL,
            date DATE NOT NULL,
            close NUMERIC(32, 10) NOT NULL,
            PRIMARY KEY (base_currency_code, quote_currency_code, date)
        )
        """,
        """
        CREATE TABLE yahoo_fund_profiles (
            symbol TEXT NOT NULL,
            family TEXT,
            category_name TEXT,
            legal_type TEXT,
            description TEXT,
            manager_name TEXT,
            annual_expense_ratio REAL,
            annual_holdings_turnover REAL,
            total_net_assets REAL,
            extracted_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE yahoo_fund_metrics (
            symbol TEXT NOT NULL,
            metric_group TEXT NOT NULL,
            metric TEXT NOT NULL,
            value_text TEXT,
            value_number REAL,
            extracted_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE yahoo_fund_performance (
            symbol TEXT NOT NULL,
            performance_type TEXT NOT NULL,
            period TEXT NOT NULL,
            as_of_date TEXT NOT NULL,
            value REAL,
            category_value REAL,
            extracted_at TEXT NOT NULL
        )
        """,
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)

        connection.exec_driver_sql(
            "INSERT INTO accounts (id, currency_code) VALUES (1, 'EUR')"
        )
        connection.exec_driver_sql(
            "INSERT INTO portfolios (id, account_id, name) "
            "VALUES (10, 1, 'Test Portfolio')"
        )
        connection.exec_driver_sql(
            """
            INSERT INTO securities
                (id, ticker, name, asset_class, currency_code)
            VALUES
                (1, 'GBPFUND.L', 'GBP Fund', 'ETF', 'GBP'),
                (2, 'USFUND', 'US Fund', 'ETF', 'USD'),
                (3, 'NOPRICE', 'Unpriced Fund', 'ETF', 'EUR')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO transactions
                (id, portfolio_id, security_id, date, type, quantity, price,
                 fees, total_value, currency_exchange_rate)
            VALUES
                (1, 10, NULL, '2025-01-01', 'DEPOSIT', 0, 0, 0, 1000, 1),
                (2, 10, 1, '2025-01-02', 'BUY', 10, 10, 0, 100, 1),
                (3, 10, 1, '2025-01-03', 'SELL', 2, 12, 0, 24, 1),
                (4, 10, 2, '2025-01-04', 'BUY', 2, 40, 0, 80, 1)
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO price_history (security_id, date, close)
            VALUES
                (1, '2025-01-03', 18),
                (1, '2025-01-05', 20),
                (2, '2025-01-05', 50)
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO fx_rate_history
                (base_currency_code, quote_currency_code, date, close)
            VALUES
                ('GBP', 'USD', '2025-01-01', 1.20),
                ('EUR', 'USD', '2025-01-01', 1.00),
                ('GBP', 'USD', '2025-01-05', 1.25),
                ('EUR', 'USD', '2025-01-05', 1.00)
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO yahoo_fund_profiles
                (symbol, family, category_name, legal_type, description,
                 manager_name, annual_expense_ratio, annual_holdings_turnover,
                 total_net_assets, extracted_at)
            VALUES
                ('GBPFUND.L', 'Example', 'Global', 'ETF', 'Example fund',
                 'A Manager', 0.0015, 0.12, 500000000, '2025-01-05T00:00:00Z')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO yahoo_fund_metrics
                (symbol, metric_group, metric, value_text, value_number, extracted_at)
            VALUES
                ('GBPFUND.L', 'risk', 'threeYearAlpha', NULL, 1.2,
                 '2025-01-05T00:00:00Z'),
                ('GBPFUND.L', 'risk', 'threeYearStandardDeviation', NULL, 9.4,
                 '2025-01-05T00:00:00Z'),
                ('GBPFUND.L', 'valuation', 'priceToBook', NULL, 2.1,
                 '2025-01-05T00:00:00Z')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO yahoo_fund_performance
                (symbol, performance_type, period, as_of_date, value,
                 category_value, extracted_at)
            VALUES
                ('GBPFUND.L', 'trailing_return', 'oneYear', '2025-01-05',
                 0.08, 0.07, '2025-01-05T00:00:00Z')
            """
        )
    return engine


def test_reconstructs_holdings_cash_and_cross_currency_values(epic2_engine):
    valuation = PortfolioValuationService(epic2_engine).value_portfolio(10)

    assert valuation.currency == "EUR"
    assert valuation.cash_balance == Decimal("844")
    assert valuation.holdings_value == Decimal("300.000")
    assert valuation.total_value == Decimal("1144.000")
    assert valuation.is_complete

    holdings = {holding.ticker: holding for holding in valuation.holdings}
    assert holdings["GBPFUND.L"].quantity == Decimal("8")
    assert holdings["GBPFUND.L"].latest_close == Decimal("20")
    assert holdings["GBPFUND.L"].fx_rate == Decimal("1.25")
    assert holdings["GBPFUND.L"].market_value == Decimal("200.00")
    assert holdings["USFUND"].market_value == Decimal("100.0")


def test_uses_prices_and_fx_rates_on_or_before_as_of_date(epic2_engine):
    valuation = PortfolioValuationService(epic2_engine).value_portfolio(
        10,
        as_of=date(2025, 1, 3),
    )

    assert [holding.ticker for holding in valuation.holdings] == ["GBPFUND.L"]
    assert valuation.holdings[0].latest_close == Decimal("18")
    assert valuation.cash_balance == Decimal("924")


def test_missing_market_data_is_explicit(epic2_engine):
    with epic2_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO transactions
                (id, portfolio_id, security_id, date, type, quantity, price,
                 fees, total_value, currency_exchange_rate)
            VALUES (5, 10, 3, '2025-01-05', 'BUY', 1, 10, 0, 10, 1)
            """
        )

    service = PortfolioValuationService(epic2_engine)
    with pytest.raises(MissingMarketDataError, match=r"NOPRICE \(price\)"):
        service.value_portfolio(10)

    incomplete = service.value_portfolio(10, strict=False)
    assert not incomplete.is_complete
    unpriced = next(
        holding for holding in incomplete.holdings if holding.ticker == "NOPRICE"
    )
    assert unpriced.market_value is None


def test_extracts_profiles_risk_metrics_performance_and_json(epic2_engine):
    extractor = FundDataExtractor(epic2_engine)

    result = extractor.extract_asset_class("etf")
    fund = next(
        security
        for security in result["securities"]
        if security["ticker"] == "GBPFUND.L"
    )

    assert result["asset_class_code"] == "ETF"
    assert result["security_count"] == 3
    assert fund["profile"]["annual_expense_ratio"] == 0.0015
    assert fund["profile"]["total_net_assets"] == 500000000
    assert {metric["metric"] for metric in fund["risk_metrics"]} == {
        "threeYearAlpha",
        "threeYearStandardDeviation",
    }
    assert fund["performance"][0]["period"] == "oneYear"

    serialized = json.loads(extractor.extract_asset_class_json("ETF"))
    assert serialized["securities"][0]["ticker"] == "GBPFUND.L"
