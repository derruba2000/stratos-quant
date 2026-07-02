from __future__ import annotations

import argparse
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Iterable, Sequence

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from dotenv import load_dotenv

from stratos_quant.config import AppConfig, load_settings
from stratos_quant.db import create_sqlite_engine
from stratos_quant.strategy import AllocationResult
from stratos_quant.ui.controller import DashboardController


ALLOCATION_MODELS = ("Hierarchical", "Ensemble")
ACCOUNT_MODES = ("all", "live", "test")
PARALLEL_WORKERS_ENV = "PARALLEL_WORKERS"
DEFAULT_PARALLEL_WORKERS = 4
SIGNALS_DIR = "signals"

_PRINT_LOCK = threading.Lock()


def _progress(message: str) -> None:
    """Thread-safe progress print flushed immediately."""
    with _PRINT_LOCK:
        print(message, flush=True)


def _load_parallel_workers(env_file: str | Path = ".env") -> int:
    """Return the number of parallel workers from PARALLEL_WORKERS env var.

    The ``.env`` file is loaded first (without overriding variables that are
    already present in the process environment).
    """
    load_dotenv(dotenv_path=env_file, override=False)
    raw = os.getenv(PARALLEL_WORKERS_ENV, "").strip()
    try:
        workers = int(raw)
        if workers > 0:
            return workers
    except ValueError:
        pass
    return DEFAULT_PARALLEL_WORKERS



@dataclass(frozen=True, slots=True)
class PortfolioBatchTarget:
    portfolio_id: int
    portfolio_name: str
    account_name: str
    currency_code: str
    is_simulated: bool


@dataclass(frozen=True, slots=True)
class StrategyReport:
    portfolio: PortfolioBatchTarget
    model_name: str
    run_id: int | None
    status: str
    path: Path


def discover_active_portfolios(
    engine: Engine,
    *,
    account_mode: str = "all",
    portfolio_names: Iterable[str] = (),
    account_names: Iterable[str] = (),
) -> tuple[PortfolioBatchTarget, ...]:
    """Return active portfolios, optionally filtered by account mode/name."""
    if account_mode not in ACCOUNT_MODES:
        raise ValueError(f"account_mode must be one of: {', '.join(ACCOUNT_MODES)}")
    names = {name.casefold() for name in portfolio_names if name}
    acct_names = {name.casefold() for name in account_names if name}

    where = ["p.is_active = 1", "a.is_active = 1"]
    parameters: dict[str, object] = {}
    if account_mode == "live":
        where.append("a.is_simulated = 0")
    elif account_mode == "test":
        where.append("a.is_simulated = 1")
    if names:
        placeholders = []
        for index, name in enumerate(sorted(names)):
            key = f"portfolio_name_{index}"
            placeholders.append(f":{key}")
            parameters[key] = name
        where.append(f"LOWER(p.name) IN ({', '.join(placeholders)})")
    if acct_names:
        placeholders = []
        for index, name in enumerate(sorted(acct_names)):
            key = f"account_name_{index}"
            placeholders.append(f":{key}")
            parameters[key] = name
        where.append(f"LOWER(a.name) IN ({', '.join(placeholders)})")

    query = f"""
        SELECT
            p.id AS portfolio_id,
            p.name AS portfolio_name,
            a.name AS account_name,
            a.currency_code,
            a.is_simulated
        FROM portfolios p
        JOIN accounts a ON a.id = p.account_id
        WHERE {' AND '.join(where)}
        ORDER BY a.name, p.name, p.id
    """
    with engine.connect() as connection:
        rows = connection.execute(text(query), parameters).mappings().all()
    return tuple(
        PortfolioBatchTarget(
            portfolio_id=int(row["portfolio_id"]),
            portfolio_name=str(row["portfolio_name"]),
            account_name=str(row["account_name"]),
            currency_code=str(row["currency_code"]),
            is_simulated=bool(row["is_simulated"]),
        )
        for row in rows
    )


class StrategyBatchRunner:
    """Run allocation workflows for many portfolios and write Markdown reports.

    Signals are computed **once per model** at the start of each batch run and
    saved to ``signals_dir/YYYYMMDD_HHMM_SS_<model>_signals.md``.  The
    pre-computed :class:`AllocationResult` is then forwarded to every portfolio
    analysis so the price-history scan is never repeated.

    Analysis tasks are dispatched to a thread pool whose size is read from the
    ``PARALLEL_WORKERS`` environment variable (default: 4).
    """

    def __init__(
        self,
        controller: DashboardController,
        *,
        output_dir: Path | str = "strategies",
        signals_dir: Path | str = SIGNALS_DIR,
        clock: object | None = None,
    ) -> None:
        self.controller = controller
        self.output_dir = Path(output_dir)
        self.signals_dir = Path(signals_dir)
        self._clock = clock or datetime

    def run(
        self,
        portfolios: Sequence[PortfolioBatchTarget],
        *,
        models: Sequence[str] = ALLOCATION_MODELS,
    ) -> tuple[StrategyReport, ...]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.signals_dir.mkdir(parents=True, exist_ok=True)

        num_workers = _load_parallel_workers()
        total = len(portfolios) * len(models)
        _progress(
            f"[batch] Starting {total} analysis task(s) "
            f"({len(portfolios)} portfolio(s) × {len(models)} model(s)) "
            f"with {num_workers} worker(s)."
        )

        # ── Step 1: compute signals once per model ────────────────────────────
        allocations: dict[str, AllocationResult | None] = {}
        can_precompute = hasattr(self.controller, "engines")
        for model_name in models:
            if model_name not in ALLOCATION_MODELS:
                raise ValueError(
                    f"model must be one of: {', '.join(ALLOCATION_MODELS)}"
                )
            if not can_precompute:
                allocations[model_name] = None
                continue
            _progress(
                f"[batch] Computing market signals for model '{model_name}' ..."
            )
            engine = self.controller.engines[model_name]
            allocation = engine.run(
                asset_class_map=getattr(self.controller, "asset_class_map", {})
            )
            allocations[model_name] = allocation

            timestamp = self._clock.now().strftime("%Y%m%d_%H%M_%S")
            sig_path = (
                self.signals_dir
                / f"{timestamp}_{_slugify(model_name)}_signals.md"
            )
            sig_path.write_text(
                _render_signals(model_name, allocation),
                encoding="utf-8",
            )
            _progress(
                f"[batch] Signals for '{model_name}' saved → {sig_path}"
            )

        # ── Step 2: dispatch portfolio × model tasks to worker pool ──────────
        work_items: list[
            tuple[PortfolioBatchTarget, str, AllocationResult | None]
        ] = [
            (portfolio, model_name, allocations[model_name])
            for portfolio in portfolios
            for model_name in models
        ]

        reports: list[StrategyReport] = []
        lock = threading.Lock()

        def _run_work_item(
            item: tuple[PortfolioBatchTarget, str, AllocationResult | None],
        ) -> None:
            portfolio, model_name, allocation = item
            report = self._run_one(
                portfolio, model_name, precomputed_allocation=allocation
            )
            with lock:
                reports.append(report)

        _progress(
            f"[batch] Dispatching {len(work_items)} task(s) "
            f"to {num_workers} worker(s) ..."
        )
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(_run_work_item, item): item
                for item in work_items
            }
            for future in as_completed(futures):
                future.result()  # re-raise any worker exception

        _progress(f"[batch] All {len(reports)} task(s) complete.")
        return tuple(reports)

    def _run_one(
        self,
        portfolio: PortfolioBatchTarget,
        model_name: str,
        *,
        precomputed_allocation: AllocationResult | None = None,
    ) -> StrategyReport:
        label = (
            f"[{portfolio.account_name}/{portfolio.portfolio_name} "
            f"(ID={portfolio.portfolio_id})]"
        )
        _progress(f"{label} → Starting {model_name} analysis ...")
        if precomputed_allocation is None:
            (
                current,
                target,
                kpis,
                components,
                rationale,
                orders_note,
                trades,
                run_id,
                status,
            ) = self.controller.run_analysis(portfolio.portfolio_id, model_name)
        else:
            (
                current,
                target,
                kpis,
                components,
                rationale,
                orders_note,
                trades,
                run_id,
                status,
            ) = self.controller.run_analysis(
                portfolio.portfolio_id,
                model_name,
                precomputed_allocation=precomputed_allocation,
            )
        _progress(
            f"{label} → Done (run_id={run_id}, status={status!r})"
        )
        timestamp = self._clock.now().strftime("%Y%m%d_%H%M%S")
        filename = (
            f"{timestamp}_{_slugify(portfolio.portfolio_name)}_"
            f"{_slugify(model_name)}.md"
        )
        path = self.output_dir / filename
        path.write_text(
            _render_report(
                portfolio=portfolio,
                model_name=model_name,
                run_id=run_id,
                status=status,
                allocation=precomputed_allocation,
                current=current,
                target=target,
                kpis=kpis,
                components=components,
                rationale=rationale,
                orders_note=orders_note,
                trades=trades,
            ),
            encoding="utf-8",
        )
        _progress(f"{label} → Report written → {path}")
        return StrategyReport(
            portfolio=portfolio,
            model_name=model_name,
            run_id=run_id,
            status=status,
            path=path,
        )


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_")
    return cleaned or "portfolio"


def _render_signals(model_name: str, allocation: AllocationResult) -> str:
    """Render an AllocationResult's signals to a human-readable Markdown string."""
    sections = [
        f"# Signals: {model_name}",
        "",
        f"- Model: `{allocation.model}`",
        f"- As of: `{allocation.as_of}`",
        "",
        "## Asset-Class Weights",
        _dataframe_to_markdown(
            pd.DataFrame(
                [
                    {
                        "Asset Class": code,
                        "Weight": format(weight, ".2f"),
                    }
                    for code, weight in sorted(allocation.weights.items())
                ]
            )
        ),
        "",
        "## Asset-Class Signals",
    ]
    if allocation.signals:
        sections.append(
            _dataframe_to_markdown(
                pd.DataFrame([s.to_dict() for s in allocation.signals])
            )
        )
    else:
        sections.append("_No asset-class signals._")
    sections.append("")
    sections.append("## Security Signals")
    if allocation.security_signals:
        sections.append(
            _dataframe_to_markdown(
                pd.DataFrame([s.to_dict() for s in allocation.security_signals])
            )
        )
    else:
        sections.append("_No security signals._")
    sections.append("")
    if allocation.component_weights:
        sections.append("## Ensemble Component Weights")
        rows = [
            {
                "Component": component,
                "Asset Class": asset_class_code,
                "Weight": format(weight, ".2f"),
            }
            for component, weights in allocation.component_weights.items()
            for asset_class_code, weight in sorted(weights.items())
        ]
        sections.append(_dataframe_to_markdown(pd.DataFrame(rows)))
        sections.append("")
    return "\n".join(sections)


def _render_report(
    *,
    portfolio: PortfolioBatchTarget,
    model_name: str,
    run_id: int | None,
    status: str,
    allocation: AllocationResult | None,
    current: pd.DataFrame,
    target: pd.DataFrame,
    kpis: pd.DataFrame,
    components: pd.DataFrame,
    rationale: str,
    orders_note: str,
    trades: pd.DataFrame,
) -> str:
    account_mode = "test" if portfolio.is_simulated else "live"
    sections = [
        f"# {portfolio.portfolio_name} - {model_name}",
        "",
        f"- Portfolio ID: `{portfolio.portfolio_id}`",
        f"- Account: `{portfolio.account_name}`",
        f"- Account mode: `{account_mode}`",
        f"- Currency: `{portfolio.currency_code}`",
        f"- Run ID: `{run_id if run_id is not None else 'not created'}`",
        f"- Run status: {status}",
        "",
        f"## Current Assets (values in {portfolio.currency_code})",
        _dataframe_to_markdown(
            current.rename(columns={"Value": f"Value ({portfolio.currency_code})"})
        ),
        "",
        "## Target Allocation And Drift",
        _dataframe_to_markdown(target),
        "",
    ]
    # ── Asset-class weights & signals from the allocation model ───────────────
    if allocation is not None:
        sections += [
            "## Asset-Class Weights",
            _dataframe_to_markdown(
                pd.DataFrame(
                    [
                        {
                            "Asset Class": code,
                            "Weight": format(weight, ".2f"),
                        }
                        for code, weight in sorted(allocation.weights.items())
                    ]
                )
            ),
            "",
            "## Asset-Class Signals",
            (
                _dataframe_to_markdown(
                    pd.DataFrame([s.to_dict() for s in allocation.signals])
                )
                if allocation.signals
                else "_No asset-class signals._"
            ),
            "",
        ]
    sections += [
        "## Strategy KPI Table",
        _dataframe_to_markdown(kpis),
        "",
        "## Ensemble Component Weights",
        _dataframe_to_markdown(components),
        "",
        "## LLM Strategy Rationale",
        rationale or "_No rationale returned._",
        "",
        "## Generated Recommendations",
        orders_note,
        "",
        _dataframe_to_markdown(trades),
        "",
    ]
    return "\n".join(sections)


def _dataframe_to_markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    columns = [str(column) for column in frame.columns]
    rows = [
        [
            _format_cell(value)
            for value in row
        ]
        for row in frame.itertuples(index=False, name=None)
    ]
    widths = [
        max(len(column), *(len(row[index]) for row in rows))
        for index, column in enumerate(columns)
    ]
    header = "| " + " | ".join(
        column.ljust(widths[index])
        for index, column in enumerate(columns)
    ) + " |"
    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    body = [
        "| " + " | ".join(
            row[index].ljust(widths[index])
            for index in range(len(columns))
        ) + " |"
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def _format_cell(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).replace("\n", " ")


def run_strategy_batch(
    *,
    settings: AppConfig | None = None,
    account_mode: str = "all",
    portfolio_names: Iterable[str] = (),
    account_names: Iterable[str] = (),
    models: Sequence[str] = ALLOCATION_MODELS,
    output_dir: Path | str = "strategies",
) -> tuple[StrategyReport, ...]:
    resolved_settings = settings or load_settings()
    engine = create_sqlite_engine(resolved_settings)
    controller = DashboardController(engine, settings=resolved_settings)
    portfolios = discover_active_portfolios(
        engine,
        account_mode=account_mode,
        portfolio_names=portfolio_names,
        account_names=account_names,
    )
    if not portfolios:
        _progress("[batch] No active portfolios matched the given filters.")
        return ()
    return StrategyBatchRunner(controller, output_dir=output_dir).run(
        portfolios,
        models=models,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run strategy workflows for active portfolios."
    )
    parser.add_argument(
        "--account-mode",
        choices=ACCOUNT_MODES,
        default="all",
        help="Filter active accounts by mode (all/live/test).",
    )
    parser.add_argument(
        "--account-name",
        action="append",
        default=[],
        help=(
            "Account name to include. May be repeated. "
            "Case-insensitive; combined with --account-mode."
        ),
    )
    parser.add_argument(
        "--portfolio-name",
        action="append",
        default=[],
        help="Exact active portfolio name to include. May be repeated.",
    )
    parser.add_argument(
        "--model",
        action="append",
        choices=ALLOCATION_MODELS,
        default=[],
        help="Allocation model to run. May be repeated. Defaults to both.",
    )
    parser.add_argument(
        "--output-dir",
        default="strategies",
        help="Directory for Markdown strategy reports.",
    )
    args = parser.parse_args(argv)
    reports = run_strategy_batch(
        account_mode=args.account_mode,
        portfolio_names=args.portfolio_name,
        account_names=args.account_name,
        models=tuple(args.model) or ALLOCATION_MODELS,
        output_dir=args.output_dir,
    )
    for report in reports:
        run_id = report.run_id if report.run_id is not None else "not created"
        print(
            f"{report.path} | portfolio={report.portfolio.portfolio_name} "
            f"| account={report.portfolio.account_name} "
            f"| model={report.model_name} | run_id={run_id} | {report.status}"
        )


if __name__ == "__main__":
    main()
