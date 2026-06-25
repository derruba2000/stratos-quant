from __future__ import annotations

from datetime import date
from decimal import Decimal
import json

import pytest
from sqlalchemy import create_engine, inspect, text

from stratos_quant.config import AppConfig
from stratos_quant.llm import (
    AdvisoryPipeline,
    OllamaClient,
    OllamaResponseError,
    StrategyRepository,
)
from stratos_quant.llm.prompts import allocation_prompt, screening_prompt
from stratos_quant.strategy import AllocationResult, AssetClassSignal


class FakeResponse:
    def __init__(self, body, *, status_error=None):
        self._body = body
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, *, json, timeout):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.response


class FakeOllamaClient:
    def __init__(self, settings, *, rationale, screening_response):
        self.settings = settings
        self.rationale = rationale
        self.screening_response = screening_response
        self.chat_calls = []
        self.json_calls = []

    def chat(self, **kwargs):
        self.chat_calls.append(kwargs)
        return self.rationale

    def chat_json(self, **kwargs):
        self.json_calls.append(kwargs)
        return self.screening_response


@pytest.fixture
def allocation():
    return AllocationResult(
        model="HIERARCHICAL",
        as_of=date(2026, 6, 25),
        weights={"ETF": Decimal("1.0000000000")},
        signals=(
            AssetClassSignal(
                asset_class_code="ETF",
                trend_positive=True,
                momentum_12m=0.24,
                annualized_volatility=0.13,
                security_count=2,
            ),
        ),
    )


@pytest.fixture
def advisory_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'advisory.sqlite3'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE portfolios (id INTEGER PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE asset_classes (code VARCHAR(32) PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE securities (id INTEGER PRIMARY KEY, ticker TEXT)"
        )
        connection.exec_driver_sql("INSERT INTO portfolios VALUES (7)")
        connection.exec_driver_sql("INSERT INTO asset_classes VALUES ('ETF')")
        connection.exec_driver_sql(
            "INSERT INTO securities VALUES (10, 'LOWFEE'), (11, 'OWNED')"
        )
    return engine


def test_ollama_client_uses_configured_endpoint_model_and_json_schema(tmp_path):
    db_file = tmp_path / "db.sqlite3"
    db_file.touch()
    settings = AppConfig(
        sqlite_db_path=db_file,
        ollama_model="gemma4",
        ollama_base_url="http://localhost:11434",
    )
    session = FakeSession(
        FakeResponse({"message": {"content": '{"recommendations":[]}'}})
    )

    result = OllamaClient(settings, session=session, timeout=9).chat_json(
        system_prompt="system",
        user_prompt="user",
        response_schema={"type": "object"},
    )

    assert result == {"recommendations": []}
    call = session.calls[0]
    assert call["url"] == "http://localhost:11434/api/chat"
    assert call["timeout"] == 9
    assert call["json"]["model"] == "gemma4"
    assert call["json"]["stream"] is False
    assert call["json"]["format"] == {"type": "object"}


def test_prompts_include_strategy_evidence_and_candidate_kpis(allocation):
    rationale_prompt = allocation_prompt(allocation)
    assert '"trend_positive": true' in rationale_prompt
    assert '"momentum_12m": 0.24' in rationale_prompt
    assert '"annualized_volatility": 0.13' in rationale_prompt
    assert '"ETF": "1.0000000000"' in rationale_prompt

    candidates = {
        "securities": [
            {
                "security_id": 10,
                "ticker": "LOWFEE",
                "profile": {
                    "annual_expense_ratio": 0.001,
                    "total_net_assets": 5000000,
                },
                "metrics": [{"metric": "alpha", "value_number": 1.2}],
                "performance": [{"period": "oneYear", "value": 0.08}],
            }
        ]
    }
    prompt = screening_prompt(
        asset_class_code="ETF",
        target_weight="1.0000000000",
        candidate_context=candidates,
        held_security_ids={11},
    )
    payload = json.loads(prompt)
    assert payload["target_asset_class_weight"] == "1.0000000000"
    assert payload["held_security_ids"] == [11]
    assert (
        payload["candidate_fundamentals"]["securities"][0]["profile"][
            "annual_expense_ratio"
        ]
        == 0.001
    )


def test_pipeline_persists_rationale_targets_and_recommendations(
    advisory_engine,
    allocation,
    tmp_path,
):
    settings = AppConfig(
        sqlite_db_path=tmp_path / "unused.sqlite3",
        ollama_model="gemma4",
        ollama_base_url="http://localhost:11434",
    )
    client = FakeOllamaClient(
        settings,
        rationale="Positive trend and momentum support the target.",
        screening_response={
            "recommendations": [
                {
                    "security_id": 10,
                    "ticker": "LOWFEE",
                    "action_type": "BUY",
                    "target_weight": 1.0,
                    "rationale": "Lower expense ratio and stronger performance.",
                }
            ]
        },
    )
    repository = StrategyRepository(advisory_engine)
    pipeline = AdvisoryPipeline(client, repository)

    run_id = pipeline.rationalize_allocation(
        portfolio_id=7,
        allocation=allocation,
    )
    recommendations = pipeline.screen_asset_class(
        run_id=run_id,
        portfolio_id=7,
        asset_class_code="ETF",
        target_weight=Decimal("1.0"),
        candidate_context={
            "asset_class_code": "ETF",
            "securities": [
                {
                    "security_id": 10,
                    "ticker": "LOWFEE",
                    "profile": {"annual_expense_ratio": 0.001},
                    "metrics": [],
                    "performance": [],
                }
            ],
        },
        held_security_ids={11},
    )

    assert recommendations[0].ticker == "LOWFEE"
    assert set(inspect(advisory_engine).get_table_names()) >= {
        "strategy_runs",
        "strategy_target_allocations",
        "asset_recommendations",
    }
    with advisory_engine.connect() as connection:
        run = connection.execute(
            text("SELECT * FROM strategy_runs WHERE id = :id"),
            {"id": run_id},
        ).mappings().one()
        target = connection.execute(
            text("SELECT * FROM strategy_target_allocations WHERE run_id = :id"),
            {"id": run_id},
        ).mappings().one()
        recommendation = connection.execute(
            text("SELECT * FROM asset_recommendations WHERE run_id = :id"),
            {"id": run_id},
        ).mappings().one()

    assert run["llm_model_used"] == "gemma4"
    assert run["llm_overall_rationale"].startswith("Positive trend")
    assert Decimal(str(target["target_weight"])) == Decimal("1")
    assert recommendation["action_type"] == "BUY"
    assert Decimal(str(recommendation["estimated_trade_value"])) == Decimal("0")
    assert recommendation["is_executed"] == 0


def test_pipeline_rejects_hallucinated_candidate_before_persistence(
    advisory_engine,
    allocation,
    tmp_path,
):
    settings = AppConfig(
        sqlite_db_path=tmp_path / "unused.sqlite3",
        ollama_model="gemma4",
        ollama_base_url="http://localhost:11434",
    )
    client = FakeOllamaClient(
        settings,
        rationale="Rationale",
        screening_response={
            "recommendations": [
                {
                    "security_id": 999,
                    "ticker": "INVENTED",
                    "action_type": "BUY",
                    "target_weight": 1,
                    "rationale": "Invented asset.",
                }
            ]
        },
    )
    pipeline = AdvisoryPipeline(client, StrategyRepository(advisory_engine))
    run_id = pipeline.rationalize_allocation(portfolio_id=7, allocation=allocation)

    with pytest.raises(OllamaResponseError, match="not in candidate context"):
        pipeline.screen_asset_class(
            run_id=run_id,
            portfolio_id=7,
            asset_class_code="ETF",
            target_weight=Decimal("1"),
            candidate_context={
                "securities": [{"security_id": 10, "ticker": "LOWFEE"}]
            },
        )

    with advisory_engine.connect() as connection:
        count = connection.execute(
            text("SELECT COUNT(*) FROM asset_recommendations")
        ).scalar_one()
    assert count == 0
