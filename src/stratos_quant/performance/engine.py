from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping

import numpy as np
import pandas as pd

from .models import (
    AllocationKPIs,
    BenchmarkKPIs,
    PerformanceReport,
    ReturnKPIs,
    RiskAdjustedKPIs,
    RiskKPIs,
    TradingCostKPIs,
)


TRADING_DAYS = 252
MONEY = Decimal("0.01")


class PerformanceKPIEngine:
    """Calculate Epic 7 strategy, benchmark, cost, and allocation KPIs."""

    def calculate(
        self,
        equity_curve: pd.DataFrame,
        *,
        trades: pd.DataFrame | None = None,
        positions: pd.DataFrame | None = None,
        benchmark_curve: pd.DataFrame | None = None,
        scope: str = "PORTFOLIO",
        benchmark: str | None = None,
        risk_free_rate: float = 0.0,
        rebalance_frequency: str = "MANUAL",
        max_asset_weight: float | None = None,
        asset_returns: pd.DataFrame | None = None,
    ) -> PerformanceReport:
        equity = _series_from_curve(equity_curve, "equity")
        if equity.empty:
            raise ValueError("equity_curve must contain at least one equity point")
        returns = equity.pct_change().dropna()
        gross_equity = _gross_equity_series(equity_curve, equity)
        benchmark_series = (
            _series_from_curve(benchmark_curve, "benchmark_equity")
            if benchmark_curve is not None
            else _series_from_curve(equity_curve, "benchmark_equity")
        )
        benchmark_returns = benchmark_series.pct_change().dropna()
        trades_frame = _coerce_frame(trades)
        positions_frame = _coerce_frame(positions)
        as_of = pd.Timestamp(equity.index.max()).date()

        return_kpis = _return_kpis(equity, gross_equity)
        risk_kpis = _risk_kpis(equity, returns)
        benchmark_kpis = _benchmark_kpis(
            equity,
            benchmark_series,
            return_kpis.total_return,
        )
        risk_adjusted = _risk_adjusted_kpis(
            returns,
            return_kpis.cagr,
            risk_kpis.downside_volatility,
            risk_kpis.volatility,
            risk_kpis.max_drawdown,
            benchmark_returns,
            risk_free_rate,
        )
        trading = _trading_cost_kpis(
            equity,
            gross_equity,
            trades_frame,
            rebalance_frequency,
            years=_years(equity),
        )
        allocation = _allocation_kpis(
            positions_frame,
            equity,
            asset_returns,
            benchmark_returns,
            max_asset_weight,
        )
        flags = _flags(
            benchmark_kpis,
            trading,
            allocation,
        )
        return PerformanceReport(
            scope=scope.upper(),
            as_of=as_of,
            risk_free_rate=risk_free_rate,
            benchmark=benchmark,
            returns=return_kpis,
            risk=risk_kpis,
            risk_adjusted=risk_adjusted,
            trading_costs=trading,
            allocation=allocation,
            benchmark_kpis=benchmark_kpis,
            flags=flags,
            explanations=_explanations(return_kpis, risk_kpis, risk_adjusted, trading, allocation),
        )


def _series_from_curve(frame: pd.DataFrame | None, column: str) -> pd.Series:
    if frame is None or frame.empty or column not in frame.columns:
        return pd.Series(dtype=float)
    date_column = "timestamp" if "timestamp" in frame.columns else "date"
    series = pd.Series(
        frame[column].astype(float).to_numpy(),
        index=pd.to_datetime(frame[date_column]),
    ).sort_index()
    return series.dropna()


def _gross_equity_series(frame: pd.DataFrame, equity: pd.Series) -> pd.Series:
    fees = _series_from_curve(frame, "fees_paid").reindex(equity.index).ffill().fillna(0)
    slippage = (
        _series_from_curve(frame, "slippage_paid")
        .reindex(equity.index)
        .ffill()
        .fillna(0)
    )
    return equity + fees + slippage


def _coerce_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    return pd.DataFrame() if frame is None else frame.copy()


def _return_kpis(equity: pd.Series, gross_equity: pd.Series) -> ReturnKPIs:
    monthly = equity.resample("ME").last().pct_change().dropna()
    quarterly = equity.resample("QE").last().pct_change().dropna()
    annual = equity.resample("YE").last().pct_change().dropna()
    return ReturnKPIs(
        total_return=float(equity.iloc[-1] / equity.iloc[0] - 1.0),
        cagr=_cagr(equity),
        monthly_return={
            index.strftime("%Y-%m"): float(value) for index, value in monthly.items()
        },
        annual_return={
            str(index.year): float(value) for index, value in annual.items()
        },
        best_month=float(monthly.max()) if not monthly.empty else None,
        worst_month=float(monthly.min()) if not monthly.empty else None,
        best_quarter=float(quarterly.max()) if not quarterly.empty else None,
        worst_quarter=float(quarterly.min()) if not quarterly.empty else None,
        before_fees_total_return=float(gross_equity.iloc[-1] / gross_equity.iloc[0] - 1.0),
        before_fees_cagr=_cagr(gross_equity),
    )


def _risk_kpis(equity: pd.Series, returns: pd.Series) -> RiskKPIs:
    downside = returns[returns < 0]
    monthly = equity.resample("ME").last().pct_change().dropna()
    var = float(returns.quantile(0.05)) if not returns.empty else None
    cvar = (
        float(returns[returns <= var].mean())
        if var is not None and not returns[returns <= var].empty
        else None
    )
    drawdown = equity / equity.cummax() - 1.0
    return RiskKPIs(
        volatility=_annualized_std(returns),
        downside_volatility=_annualized_std(downside),
        max_drawdown=float(drawdown.min()) if not drawdown.empty else 0.0,
        max_drawdown_duration=_max_drawdown_duration(drawdown),
        value_at_risk=var,
        conditional_value_at_risk=cvar,
        worst_day=float(returns.min()) if not returns.empty else None,
        worst_month=float(monthly.min()) if not monthly.empty else None,
    )


def _risk_adjusted_kpis(
    returns: pd.Series,
    cagr: float,
    downside_volatility: float,
    volatility: float,
    max_drawdown: float,
    benchmark_returns: pd.Series,
    risk_free_rate: float,
) -> RiskAdjustedKPIs:
    excess_cagr = cagr - risk_free_rate
    tracking_error = None
    information_ratio = None
    if not benchmark_returns.empty:
        active = returns.align(benchmark_returns, join="inner")
        active_returns = active[0] - active[1]
        tracking_error = _annualized_std(active_returns)
        information_ratio = (
            float(active_returns.mean() * TRADING_DAYS / tracking_error)
            if tracking_error > 0
            else None
        )
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    gross_profit = float(wins.sum())
    gross_loss = abs(float(losses.sum()))
    average_win = float(wins.mean()) if not wins.empty else None
    average_loss = float(losses.mean()) if not losses.empty else None
    win_rate = float(len(wins) / len(returns)) if len(returns) else None
    payoff = (
        abs(average_win / average_loss)
        if average_win is not None and average_loss not in {None, 0}
        else None
    )
    expectancy = (
        win_rate * average_win + (1 - win_rate) * average_loss
        if win_rate is not None
        and average_win is not None
        and average_loss is not None
        else None
    )
    return RiskAdjustedKPIs(
        sharpe_ratio=excess_cagr / volatility if volatility > 0 else None,
        sortino_ratio=excess_cagr / downside_volatility if downside_volatility > 0 else None,
        calmar_ratio=cagr / abs(max_drawdown) if max_drawdown < 0 else None,
        information_ratio=information_ratio,
        tracking_error=tracking_error,
        profit_factor=gross_profit / gross_loss if gross_loss > 0 else None,
        win_rate=win_rate,
        average_win=average_win,
        average_loss=average_loss,
        payoff_ratio=payoff,
        expectancy=expectancy,
    )


def _benchmark_kpis(
    equity: pd.Series,
    benchmark: pd.Series,
    strategy_return: float,
) -> BenchmarkKPIs:
    if benchmark.empty or len(benchmark) < 2:
        return BenchmarkKPIs(None, None, None, None, False)
    aligned_benchmark = benchmark.reindex(equity.index).dropna()
    benchmark_return = float(aligned_benchmark.iloc[-1] / aligned_benchmark.iloc[0] - 1.0)
    benchmark_drawdown = aligned_benchmark / aligned_benchmark.cummax() - 1.0
    excess_return = strategy_return - benchmark_return
    return BenchmarkKPIs(
        benchmark_return=benchmark_return,
        benchmark_cagr=_cagr(aligned_benchmark),
        benchmark_max_drawdown=float(benchmark_drawdown.min()),
        excess_return=excess_return,
        rejected_after_costs=excess_return <= 0,
    )


def _trading_cost_kpis(
    equity: pd.Series,
    gross_equity: pd.Series,
    trades: pd.DataFrame,
    rebalance_frequency: str,
    *,
    years: float,
) -> TradingCostKPIs:
    if trades.empty:
        total_fees = Decimal("0")
        total_slippage = Decimal("0")
        trade_value = Decimal("0")
        number = 0
    else:
        total_fees = _money_sum(trades, "estimated_fees")
        total_slippage = _money_sum(trades, "estimated_slippage")
        trade_value = _money_sum(trades, "trade_value")
        number = len(trades)
    average_equity = Decimal(str(equity.mean())) if equity.mean() > 0 else Decimal("0")
    turnover = float(trade_value / average_equity) if average_equity > 0 else 0.0
    fee_drag = float((gross_equity.iloc[-1] - equity.iloc[-1]) / gross_equity.iloc[0])
    cash_drag = _cash_drag(equity)
    return TradingCostKPIs(
        number_of_trades=number,
        turnover=turnover,
        annualized_turnover=turnover / years if years > 0 else turnover,
        total_fees=total_fees,
        total_slippage=total_slippage,
        average_trade_size=(
            (trade_value / Decimal(number)).quantize(MONEY, rounding=ROUND_HALF_UP)
            if number
            else Decimal("0")
        ),
        fee_drag=fee_drag,
        rebalance_frequency=rebalance_frequency.upper(),
        cash_drag=cash_drag,
    )


def _allocation_kpis(
    positions: pd.DataFrame,
    equity: pd.Series,
    asset_returns: pd.DataFrame | None,
    benchmark_returns: pd.Series,
    max_asset_weight: float | None,
) -> AllocationKPIs:
    if positions.empty:
        return AllocationKPIs({}, {}, {}, {}, {}, {}, 1.0, {}, {}, {}, (), False)
    frame = positions.copy()
    date_column = "timestamp" if "timestamp" in frame.columns else "date"
    frame[date_column] = pd.to_datetime(frame[date_column])
    frame["ticker"] = frame["ticker"].astype(str).str.upper()
    weights = frame.pivot_table(
        index=date_column,
        columns="ticker",
        values="weight",
        aggfunc="sum",
    ).fillna(0.0)
    avg = weights.mean().to_dict()
    max_weight = weights.max().to_dict()
    min_weight = weights.min().to_dict()
    breaches = []
    if max_asset_weight is not None:
        breaches.extend(
            f"{ticker} exceeded max asset weight {max_asset_weight:.2%}"
            for ticker, value in max_weight.items()
            if value > max_asset_weight
        )
    asset_class_exposure = _exposure(frame, "asset_class_code")
    sector_exposure = _exposure(frame, "sector")
    currency_exposure = _exposure(frame, "currency")
    cash_exposure = max(0.0, 1.0 - float(weights.sum(axis=1).mean()))
    concentration_risk = any(value > 0.50 for value in max_weight.values())
    if concentration_risk:
        breaches.append("Concentration risk: at least one asset exceeded 50%.")
    risk_contribution = _risk_contribution(weights, asset_returns)
    correlation_to_portfolio = _correlation_to_portfolio(weights, asset_returns, equity)
    correlation_to_benchmark = _correlation_to_benchmark(asset_returns, benchmark_returns)
    return AllocationKPIs(
        average_asset_weight={key: float(value) for key, value in avg.items()},
        maximum_asset_weight={key: float(value) for key, value in max_weight.items()},
        minimum_asset_weight={key: float(value) for key, value in min_weight.items()},
        asset_class_exposure=asset_class_exposure,
        sector_exposure=sector_exposure,
        currency_exposure=currency_exposure,
        cash_exposure=cash_exposure,
        risk_contribution=risk_contribution,
        correlation_to_portfolio=correlation_to_portfolio,
        correlation_to_benchmark=correlation_to_benchmark,
        constraint_breaches=tuple(breaches),
        concentration_risk_flag=concentration_risk,
    )


def _cagr(equity: pd.Series) -> float:
    years = _years(equity)
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0)


def _years(equity: pd.Series) -> float:
    return max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)


def _annualized_std(returns: pd.Series) -> float:
    return float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(returns) > 1 else 0.0


def _max_drawdown_duration(drawdown: pd.Series) -> int:
    current = 0
    longest = 0
    for value in drawdown:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _money_sum(frame: pd.DataFrame, column: str) -> Decimal:
    if column not in frame.columns:
        return Decimal("0")
    return sum((Decimal(str(value)) for value in frame[column]), Decimal("0")).quantize(
        MONEY,
        rounding=ROUND_HALF_UP,
    )


def _cash_drag(equity: pd.Series) -> float:
    returns = equity.pct_change().dropna()
    return float(abs(min(0.0, returns.mean())) * TRADING_DAYS) if not returns.empty else 0.0


def _exposure(frame: pd.DataFrame, column: str) -> dict[str, float]:
    if column not in frame.columns:
        return {}
    date_column = "timestamp" if "timestamp" in frame.columns else "date"
    by_date = frame.pivot_table(
        index=date_column,
        columns=column,
        values="weight",
        aggfunc="sum",
    ).fillna(0.0)
    return {
        str(key): float(value)
        for key, value in by_date.mean().items()
    }


def _risk_contribution(
    weights: pd.DataFrame,
    asset_returns: pd.DataFrame | None,
) -> dict[str, float]:
    if asset_returns is None or asset_returns.empty:
        total = weights.mean().sum()
        return {
            str(key): float(value / total) if total else 0.0
            for key, value in weights.mean().items()
        }
    aligned = asset_returns.reindex(weights.index).dropna(how="all")
    vol = aligned.std(ddof=1).fillna(0.0)
    contribution = weights.reindex(aligned.index).mean() * vol
    total = contribution.sum()
    return {
        str(key): float(value / total) if total else 0.0
        for key, value in contribution.items()
    }


def _correlation_to_portfolio(
    weights: pd.DataFrame,
    asset_returns: pd.DataFrame | None,
    equity: pd.Series,
) -> dict[str, float]:
    if asset_returns is None or asset_returns.empty:
        return {}
    portfolio_returns = equity.pct_change().dropna()
    aligned = asset_returns.align(portfolio_returns, join="inner", axis=0)[0]
    correlations: dict[str, float] = {}
    for column in aligned.columns:
        value = _safe_corr(aligned[column], portfolio_returns)
        correlations[str(column)] = value
    return correlations


def _correlation_to_benchmark(
    asset_returns: pd.DataFrame | None,
    benchmark_returns: pd.Series,
) -> dict[str, float]:
    if asset_returns is None or asset_returns.empty or benchmark_returns.empty:
        return {}
    aligned = asset_returns.align(benchmark_returns, join="inner", axis=0)[0]
    correlations: dict[str, float] = {}
    for column in aligned.columns:
        value = _safe_corr(aligned[column], benchmark_returns)
        correlations[str(column)] = value
    return correlations


def _safe_corr(left: pd.Series, right: pd.Series) -> float:
    if len(left) < 2 or left.std(ddof=1) == 0 or right.std(ddof=1) == 0:
        return 0.0
    value = left.corr(right)
    return 0.0 if np.isnan(value) else float(value)


def _flags(
    benchmark: BenchmarkKPIs,
    trading: TradingCostKPIs,
    allocation: AllocationKPIs,
) -> tuple[str, ...]:
    flags = list(allocation.constraint_breaches)
    if benchmark.rejected_after_costs:
        flags.append("Strategy failed to beat benchmark after costs.")
    if trading.annualized_turnover > 5:
        flags.append("Turnover is excessive.")
    if trading.fee_drag > 0.02:
        flags.append("Fees and slippage materially reduced performance.")
    return tuple(flags)


def _explanations(
    returns: ReturnKPIs,
    risk: RiskKPIs,
    risk_adjusted: RiskAdjustedKPIs,
    trading: TradingCostKPIs,
    allocation: AllocationKPIs,
) -> dict[str, str]:
    return {
        "returns": (
            "Total return and CAGR measure absolute growth; before-fee figures "
            "show the drag from costs."
        ),
        "risk": (
            "Volatility is annualized, downside volatility only uses negative "
            "returns, and drawdown is measured from the equity curve peak."
        ),
        "risk_adjusted": (
            "Sharpe, Sortino, Calmar, and information ratio compare return to "
            "different forms of risk."
        ),
        "trading_costs": (
            f"{trading.number_of_trades} trades produced {trading.turnover:.2f}x "
            "turnover; fee drag shows the performance lost to fees and slippage."
        ),
        "allocation": (
            "Diversification improved when average weights are balanced and "
            "correlations or concentration flags are low."
            if not allocation.concentration_risk_flag
            else "Diversification worsened because one or more assets became concentrated."
        ),
    }
