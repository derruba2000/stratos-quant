from __future__ import annotations

import os
from pathlib import Path

import gradio as gr
import pandas as pd

from stratos_quant.config import load_settings
from stratos_quant.db import create_sqlite_engine

from .controller import (
    ALLOCATION_COLUMNS,
    COMPONENT_COLUMNS,
    STRATEGY_KPI_COLUMNS,
    TARGET_COLUMNS,
    TRADE_COLUMNS,
    DashboardController,
    ORDERS_READY_MESSAGE,
)


CSS = """
.sq-shell { max-width: 1440px; margin: 0 auto; }
.sq-hero {
  border: 1px solid var(--border-color-primary);
  border-radius: 18px;
  padding: 24px 28px;
  background: linear-gradient(135deg, rgba(20, 184, 166, .13), rgba(59, 130, 246, .08));
}
.sq-status { min-height: 46px; }
.sq-rationale {
  max-height: 320px;
  overflow-y: auto;
}
.sq-orders-note {
  border-left: 4px solid var(--color-accent);
  padding: 8px 14px;
  background: var(--background-fill-secondary);
  border-radius: 10px;
}
"""


def build_app(controller: DashboardController | None = None) -> gr.Blocks:
    settings = load_settings()
    resolved_controller = controller or DashboardController(
        create_sqlite_engine(settings),
        settings=settings,
    )
    choices = resolved_controller.portfolio_choices()
    default_portfolio = choices[0][1] if choices else None

    with gr.Blocks(
        title="Stratos Quant Control Board",
    ) as app:
        run_state = gr.State(value=None)
        with gr.Column(elem_classes="sq-shell"):
            gr.Markdown(
                """
                # STRATOS-QUANT CONTROL BOARD
                Quantitative portfolio targets, local-LLM rationale, and
                executable rebalance mandates in one place.
                """,
                elem_classes="sq-hero",
            )
            gr.Markdown(
                f"**Database:** `{Path(settings.sqlite_db_path).name}` &nbsp; · "
                f"&nbsp; **LLM provider:** `{settings.llm_provider_label}` &nbsp; · "
                f"&nbsp; **Model:** `{settings.llm_model}`"
            )

            with gr.Row():
                portfolio = gr.Dropdown(
                    choices=choices,
                    value=default_portfolio,
                    label="Active portfolio",
                    info="Loaded directly from the portfolios table.",
                    scale=2,
                )
                model = gr.Radio(
                    choices=["Hierarchical", "Ensemble"],
                    value="Hierarchical",
                    label="Allocation engine",
                    scale=1,
                )
            trigger = gr.Button(
                "Trigger rebalance analysis run",
                variant="primary",
                size="lg",
            )
            status = gr.Markdown(
                "Ready to run.",
                elem_classes="sq-status",
            )

            gr.Markdown("## Portfolio analysis overview")
            with gr.Row():
                current = gr.Dataframe(
                    value=pd.DataFrame(columns=ALLOCATION_COLUMNS),
                    headers=ALLOCATION_COLUMNS,
                    datatype=["str", "number", "number"],
                    label="Current allocation summary",
                    interactive=False,
                    wrap=True,
                )
                target = gr.Dataframe(
                    value=pd.DataFrame(columns=TARGET_COLUMNS),
                    headers=TARGET_COLUMNS,
                    datatype=["str", "number", "number", "bool"],
                    label="Rebalancing target matrix",
                    interactive=False,
                    wrap=True,
                )

            gr.Markdown("## Quantitative strategy diagnostics")
            strategy_kpis = gr.Dataframe(
                value=pd.DataFrame(columns=STRATEGY_KPI_COLUMNS),
                headers=STRATEGY_KPI_COLUMNS,
                datatype=["str", "str", "bool", "number", "number", "number"],
                label="Signals computed by the allocation model",
                interactive=False,
                wrap=True,
            )
            component_weights = gr.Dataframe(
                value=pd.DataFrame(columns=COMPONENT_COLUMNS),
                headers=COMPONENT_COLUMNS,
                datatype=["str", "str", "number"],
                label="Ensemble component votes",
                interactive=False,
                wrap=True,
            )

            gr.Markdown(f"## {settings.llm_provider_label} strategic rationale log")
            rationale = gr.Markdown(
                "Run an analysis to generate the model's audit.",
                elem_classes="sq-rationale",
            )

            gr.Markdown("## Actionable rebalancing orders")
            orders_note = gr.Markdown(
                ORDERS_READY_MESSAGE,
                elem_classes="sq-orders-note",
            )
            trades = gr.Dataframe(
                value=pd.DataFrame(columns=TRADE_COLUMNS),
                headers=TRADE_COLUMNS,
                datatype=[
                    "number",
                    "str",
                    "str",
                    "str",
                    "number",
                    "number",
                    "str",
                    "bool",
                ],
                label="Check Executed to update the database",
                interactive=True,
                static_columns=list(range(7)),
                wrap=True,
                max_height=520,
                show_search="filter",
            )

            trigger.click(
                fn=resolved_controller.run_analysis,
                inputs=[portfolio, model],
                outputs=[
                    current,
                    target,
                    strategy_kpis,
                    component_weights,
                    rationale,
                    orders_note,
                    trades,
                    run_state,
                    status,
                ],
                show_progress="full",
            )
            trades.input(
                fn=resolved_controller.update_executed,
                inputs=trades,
                outputs=[trades, status],
                show_progress="minimal",
            )
    return app


def launch_network_options() -> dict[str, str | int | None]:
    """Resolve optional Gradio host/port overrides from the environment."""
    port_value = os.getenv("GRADIO_SERVER_PORT", "").strip()
    try:
        server_port = int(port_value) if port_value else None
    except ValueError as exc:
        raise ValueError("GRADIO_SERVER_PORT must be an integer") from exc
    if server_port is not None and not 1 <= server_port <= 65535:
        raise ValueError("GRADIO_SERVER_PORT must be between 1 and 65535")
    return {
        "server_name": os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"),
        "server_port": server_port,
    }


def main() -> None:
    network_options = launch_network_options()
    build_app().queue().launch(
        **network_options,
        show_error=True,
        theme=gr.themes.Soft(primary_hue="teal", secondary_hue="blue"),
        css=CSS,
    )


if __name__ == "__main__":
    main()
