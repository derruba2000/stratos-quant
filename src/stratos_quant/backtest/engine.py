from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Protocol

import numpy as np
import pandas as pd

from stratos_quant.performance import PerformanceKPIEngine
from stratos_quant.strategy import AllocationResult
from stratos_quant.strategy.models import ONE, ZERO, normalize_weights

from .models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestOrder,
    BacktestPosition,
    BacktestResult,
    BacktestTrade,
    EquityCurvePoint,
)


MONEY = Decimal("0.01")
QUANTITY = Decimal("0.0000000001")
WEIGHT = Decimal("0.0000000001")
TRADING_DAYS = 252


class AllocationStrategy(Protocol):
    def allocate(
        self,
        prices: pd.DataFrame,
        *,
        as_of: date | None = None,
    ) -> AllocationResult:
        ...


class BacktestEngine:
    """Walk-forward portfolio backtester with fees, slippage, and benchmarks."""

    def run(
        self,
        prices: pd.DataFrame,
        *,
        strategy: AllocationStrategy,
        config: BacktestConfig,
    ) -> BacktestResult:
        clean = _prepare_prices(prices, config)
        trading_dates = [
            pd.Timestamp(item).date()
            for item in sorted(clean["date"].drop_duplicates())
        ]
        if not trading_dates:
            raise ValueError("No prices are available inside the backtest window")

        symbols = tuple(sorted({symbol.upper() for symbol in config.symbols}))
        cash = Decimal(config.initial_cash)
        positions: dict[str, Decimal] = {symbol: ZERO for symbol in symbols}
        cumulative_fees = ZERO
        cumulative_slippage = ZERO
        orders: list[BacktestOrder] = []
        trades: list[BacktestTrade] = []
        position_rows: list[BacktestPosition] = []
        equity_curve: list[EquityCurvePoint] = []
        allocations: dict[date, dict[str, Decimal]] = {}
        walk_forward_dates: list[date] = []

        rebalance_dates = _rebalance_dates(
            trading_dates,
            config.rebalance_frequency,
        )
        benchmark_equity = _benchmark_equity(clean, config, trading_dates)

        for current_date in trading_dates:
            price_map = _prices_on(clean, current_date)
            equity = _portfolio_value(cash, positions, price_map)

            if current_date in rebalance_dates:
                history = clean[clean["date"] <= pd.Timestamp(current_date)].copy()
                allocation = strategy.allocate(history, as_of=current_date)
                ticker_targets = _asset_class_targets_to_tickers(
                    allocation.weights,
                    history,
                    symbols,
                )
                allocations[current_date] = ticker_targets
                walk_forward_dates.append(current_date)
                cash, fees, slippage = self._rebalance(
                    current_date=current_date,
                    cash=cash,
                    positions=positions,
                    price_map=price_map,
                    equity=equity,
                    targets=ticker_targets,
                    config=config,
                    orders=orders,
                    trades=trades,
                )
                cumulative_fees += fees
                cumulative_slippage += slippage
                equity = _portfolio_value(cash, positions, price_map)

            _record_positions(
                current_date,
                positions,
                price_map,
                equity,
                position_rows,
            )
            equity_curve.append(
                EquityCurvePoint(
                    timestamp=current_date,
                    equity=equity.quantize(MONEY, rounding=ROUND_HALF_UP),
                    cash=cash.quantize(MONEY, rounding=ROUND_HALF_UP),
                    fees_paid=cumulative_fees.quantize(MONEY),
                    slippage_paid=cumulative_slippage.quantize(MONEY),
                    benchmark_equity=benchmark_equity.get(current_date),
                )
            )

        metrics = _calculate_metrics(config, equity_curve, trades, position_rows)
        return BacktestResult(
            config=config,
            orders=tuple(orders),
            trades=tuple(trades),
            positions=tuple(position_rows),
            equity_curve=tuple(equity_curve),
            rebalance_allocations=allocations,
            metrics=metrics,
            walk_forward_windows=tuple(walk_forward_dates),
            overfitting_warnings=_overfitting_warnings(
                metrics,
                len(walk_forward_dates),
            ),
        )

    def _rebalance(
        self,
        *,
        current_date: date,
        cash: Decimal,
        positions: dict[str, Decimal],
        price_map: dict[str, Decimal],
        equity: Decimal,
        targets: dict[str, Decimal],
        config: BacktestConfig,
        orders: list[BacktestOrder],
        trades: list[BacktestTrade],
    ) -> tuple[Decimal, Decimal, Decimal]:
        fees_paid = ZERO
        slippage_paid = ZERO
        trade_plans = []
        for ticker, target_weight in targets.items():
            close = price_map.get(ticker)
            if close is None or close <= ZERO:
                continue
            current_value = positions.get(ticker, ZERO) * close
            current_weight = (
                (current_value / equity).quantize(WEIGHT)
                if equity > ZERO
                else ZERO
            )
            if abs(current_weight - target_weight) <= config.drift_threshold:
                continue
            target_value = equity * target_weight
            delta_value = target_value - current_value
            if delta_value == ZERO:
                continue
            side = "BUY" if delta_value > ZERO else "SELL"
            trade_value = abs(delta_value).quantize(MONEY, rounding=ROUND_HALF_UP)
            orders.append(
                BacktestOrder(
                    timestamp=current_date,
                    ticker=ticker,
                    side=side,
                    target_weight=target_weight,
                    current_weight=current_weight,
                    trade_value=trade_value,
                )
            )
            trade_plans.append((side, ticker, trade_value, close))

        for side, ticker, trade_value, close in sorted(
            trade_plans,
            key=lambda item: item[0] != "SELL",
        ):
            fee = (config.fixed_trade_fee + trade_value * config.broker_fee_rate).quantize(
                MONEY,
                rounding=ROUND_HALF_UP,
            )
            slippage = (trade_value * config.slippage_rate).quantize(
                MONEY,
                rounding=ROUND_HALF_UP,
            )
            if side == "BUY":
                execution_price = close * (ONE + config.slippage_rate)
                max_affordable = cash - fee
                if max_affordable <= ZERO:
                    continue
                affordable_trade_value = min(trade_value, max_affordable)
                quantity = affordable_trade_value / execution_price
                if not config.fractional_shares:
                    quantity = quantity.to_integral_value(rounding=ROUND_DOWN)
                quantity = quantity.quantize(QUANTITY)
                gross_value = (quantity * execution_price).quantize(
                    MONEY,
                    rounding=ROUND_HALF_UP,
                )
                if quantity <= ZERO or gross_value + fee > cash:
                    continue
                cash -= gross_value + fee
                positions[ticker] = positions.get(ticker, ZERO) + quantity
            else:
                execution_price = close * (ONE - config.slippage_rate)
                held = positions.get(ticker, ZERO)
                quantity = min(held, trade_value / execution_price)
                if not config.fractional_shares:
                    quantity = quantity.to_integral_value(rounding=ROUND_DOWN)
                quantity = quantity.quantize(QUANTITY)
                gross_value = (quantity * execution_price).quantize(
                    MONEY,
                    rounding=ROUND_HALF_UP,
                )
                if quantity <= ZERO:
                    continue
                positions[ticker] = max(ZERO, held - quantity)
                cash += gross_value - fee
            fees_paid += fee
            slippage_paid += slippage
            trades.append(
                BacktestTrade(
                    timestamp=current_date,
                    ticker=ticker,
                    side=side,
                    quantity=quantity,
                    execution_price=execution_price.quantize(
                        MONEY,
                        rounding=ROUND_HALF_UP,
                    ),
                    trade_value=gross_value,
                    estimated_fees=fee,
                    estimated_slippage=slippage,
                )
            )
        return cash, fees_paid, slippage_paid


def _prepare_prices(prices: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    required = {"date", "ticker", "asset_class_code", "close"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Price data is missing columns: {', '.join(sorted(missing))}")
    clean = prices.copy()
    clean["date"] = pd.to_datetime(clean["date"])
    clean["ticker"] = clean["ticker"].astype(str).str.upper()
    clean["asset_class_code"] = clean["asset_class_code"].astype(str).str.upper()
    included_symbols = {symbol.upper() for symbol in config.symbols}
    if config.benchmark:
        included_symbols.add(config.benchmark.upper())
    clean = clean[
        (clean["ticker"].isin(included_symbols))
        & (clean["date"] >= pd.Timestamp(config.start_date))
        & (clean["date"] <= pd.Timestamp(config.end_date))
    ].sort_values(["date", "ticker"])
    if clean.empty:
        raise ValueError("No matching symbol prices are available")
    if "security_id" not in clean.columns:
        ticker_ids = {
            ticker: index + 1
            for index, ticker in enumerate(sorted(clean["ticker"].unique()))
        }
        clean["security_id"] = clean["ticker"].map(ticker_ids)
    return clean


def _prices_on(prices: pd.DataFrame, current_date: date) -> dict[str, Decimal]:
    frame = prices[prices["date"] == pd.Timestamp(current_date)]
    return {
        str(row["ticker"]).upper(): Decimal(str(row["close"]))
        for row in frame.to_dict(orient="records")
    }


def _portfolio_value(
    cash: Decimal,
    positions: dict[str, Decimal],
    price_map: dict[str, Decimal],
) -> Decimal:
    return cash + sum(
        (
            quantity * price_map.get(ticker, ZERO)
            for ticker, quantity in positions.items()
        ),
        ZERO,
    )


def _rebalance_dates(
    trading_dates: list[date],
    frequency: str,
) -> set[date]:
    normalized = frequency.strip().upper()
    if normalized in {"DAILY", "D"}:
        return set(trading_dates)
    dates: list[date] = []
    previous_key = None
    for item in trading_dates:
        if normalized in {"WEEKLY", "W"}:
            key = item.isocalendar()[:2]
        elif normalized in {"MONTHLY", "M"}:
            key = (item.year, item.month)
        else:
            raise ValueError("rebalance_frequency must be DAILY, WEEKLY, or MONTHLY")
        if key != previous_key:
            dates.append(item)
            previous_key = key
    return set(dates)


def _asset_class_targets_to_tickers(
    asset_class_weights: dict[str, Decimal],
    history: pd.DataFrame,
    symbols: tuple[str, ...],
) -> dict[str, Decimal]:
    latest_date = history["date"].max()
    latest = history[history["date"] == latest_date]
    by_class: dict[str, list[str]] = defaultdict(list)
    for row in latest.to_dict(orient="records"):
        ticker = str(row["ticker"]).upper()
        if ticker in symbols:
            by_class[str(row["asset_class_code"]).upper()].append(ticker)

    targets: dict[str, Decimal] = {}
    for asset_class, class_weight in asset_class_weights.items():
        if class_weight <= ZERO or asset_class == "CASH":
            continue
        tickers = sorted(by_class.get(asset_class.upper(), []))
        if not tickers and asset_class.upper() in symbols:
            tickers = [asset_class.upper()]
        if not tickers:
            continue
        each = class_weight / Decimal(len(tickers))
        for ticker in tickers:
            targets[ticker] = targets.get(ticker, ZERO) + each
    for ticker in symbols:
        targets.setdefault(ticker, ZERO)
    positive = {code: weight for code, weight in targets.items() if weight > ZERO}
    return normalize_weights(positive) if positive else {symbols[0]: ONE}


def _record_positions(
    current_date: date,
    positions: dict[str, Decimal],
    price_map: dict[str, Decimal],
    equity: Decimal,
    rows: list[BacktestPosition],
) -> None:
    if equity <= ZERO:
        return
    for ticker, quantity in sorted(positions.items()):
        close = price_map.get(ticker)
        if close is None:
            continue
        market_value = quantity * close
        rows.append(
            BacktestPosition(
                timestamp=current_date,
                ticker=ticker,
                quantity=quantity,
                close=close,
                market_value=market_value.quantize(MONEY, rounding=ROUND_HALF_UP),
                weight=(market_value / equity).quantize(WEIGHT),
            )
        )


def _benchmark_equity(
    prices: pd.DataFrame,
    config: BacktestConfig,
    trading_dates: list[date],
) -> dict[date, Decimal | None]:
    if not config.benchmark:
        return {item: None for item in trading_dates}
    benchmark = config.benchmark.upper()
    frame = prices[prices["ticker"] == benchmark].sort_values("date")
    if frame.empty:
        return {item: None for item in trading_dates}
    first_close = Decimal(str(frame.iloc[0]["close"]))
    closes = {
        pd.Timestamp(row["date"]).date(): Decimal(str(row["close"]))
        for row in frame.to_dict(orient="records")
    }
    return {
        item: (
            (config.initial_cash * close / first_close).quantize(
                MONEY,
                rounding=ROUND_HALF_UP,
            )
            if (close := closes.get(item)) is not None
            else None
        )
        for item in trading_dates
    }


def _calculate_metrics(
    config: BacktestConfig,
    equity_curve: list[EquityCurvePoint],
    trades: list[BacktestTrade],
    positions: list[BacktestPosition],
) -> BacktestMetrics:
    equity_frame = pd.DataFrame([point.to_dict() for point in equity_curve])
    trade_frame = pd.DataFrame([trade.to_dict() for trade in trades])
    position_frame = pd.DataFrame([position.to_dict() for position in positions])
    report = PerformanceKPIEngine().calculate(
        equity_frame,
        trades=trade_frame,
        positions=position_frame,
        benchmark=config.benchmark,
        risk_free_rate=0.0,
        rebalance_frequency=config.rebalance_frequency,
    )
    return BacktestMetrics(
        total_return=report.returns.total_return,
        cagr=report.returns.cagr,
        volatility=report.risk.volatility,
        sharpe_ratio=report.risk_adjusted.sharpe_ratio,
        sortino_ratio=report.risk_adjusted.sortino_ratio,
        calmar_ratio=report.risk_adjusted.calmar_ratio,
        max_drawdown=report.risk.max_drawdown,
        turnover=report.trading_costs.turnover,
        total_fees=report.trading_costs.total_fees,
        total_slippage=report.trading_costs.total_slippage,
        benchmark_return=report.benchmark_kpis.benchmark_return,
        excess_return=report.benchmark_kpis.excess_return,
        information_ratio=report.risk_adjusted.information_ratio,
        tracking_error=report.risk_adjusted.tracking_error,
        benchmark_max_drawdown=report.benchmark_kpis.benchmark_max_drawdown,
        rejected_after_costs=report.benchmark_kpis.rejected_after_costs,
    )


def _overfitting_warnings(
    metrics: BacktestMetrics,
    walk_forward_count: int,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if walk_forward_count < 3:
        warnings.append("Too few walk-forward windows to rule out overfitting.")
    if metrics.rejected_after_costs:
        warnings.append("Strategy failed to beat benchmark after costs.")
    if metrics.turnover > 10:
        warnings.append("Turnover is high and may indicate overfitting or fee drag.")
    return tuple(warnings)
