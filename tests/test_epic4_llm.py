from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest
from openai import OpenAIError
from sqlalchemy import create_engine, inspect, text

from stratos_quant.config import AppConfig
from stratos_quant.llm import (
    AdvisoryPipeline,
    NvidiaClient,
    OllamaError,
    OllamaClient,
    OllamaResponseError,
    StrategyRepository,
    create_chat_client,
)
import stratos_quant.llm.client as llm_client_module
from stratos_quant.llm.prompts import allocation_prompt, screening_prompt
from stratos_quant.strategy import AllocationResult, AssetClassSignal, SecuritySignal


class FakeResponse:
    def __init__(self, body, *, status_error=None, status_code=200, text=""):
        self._body = body
        self._status_error = status_error
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self._status_error is not None:
            self._status_error.response = self
            raise self._status_error

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, *, json, timeout, headers=None, verify=None):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "timeout": timeout,
                "headers": headers,
                "verify": verify,
            }
        )
        return self.response


class FakeOpenAICompletions:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content)
                )
            ]
        )


class FakeOpenAIClient:
    def __init__(self, content=None, error=None):
        self.completions = FakeOpenAICompletions(content=content, error=error)
        self.chat = SimpleNamespace(completions=self.completions)


class FakeOpenAINotFoundError(OpenAIError):
    status_code = 404


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


def test_nvidia_client_uses_configured_model_key_and_json_schema(tmp_path):
    db_file = tmp_path / "db.sqlite3"
    db_file.touch()
    settings = AppConfig(
        sqlite_db_path=db_file,
        ollama_model="",
        ollama_base_url="",
        api_usage="NVIDIA",
        nvidia_api_model="nvidia/llama-3.1-nemotron",
        nvidia_api_key="secret-key",
    )
    openai_client = FakeOpenAIClient(content='{"recommendations":[]}')

    result = NvidiaClient(
        settings,
        openai_client=openai_client,
        timeout=9,
    ).chat_json(
        system_prompt="system",
        user_prompt="user",
        response_schema={"type": "object"},
    )

    assert result == {"recommendations": []}
    call = openai_client.completions.calls[0]
    assert call["model"] == "nvidia/llama-3.1-nemotron"
    assert call["max_tokens"] == 4096
    assert call["temperature"] == 0
    assert call["messages"][0] == {"role": "system", "content": "system"}
    assert call["messages"][1]["role"] == "user"
    assert call["messages"][1]["content"].startswith("user")
    assert '"type": "object"' in call["messages"][1]["content"]


def test_nvidia_client_accepts_json_wrapped_in_markdown_fence(tmp_path):
    db_file = tmp_path / "db.sqlite3"
    db_file.touch()
    settings = AppConfig(
        sqlite_db_path=db_file,
        ollama_model="",
        ollama_base_url="",
        api_usage="NVIDIA",
        nvidia_api_model="nvidia/llama-3.1-nemotron",
        nvidia_api_key="secret-key",
    )
    openai_client = FakeOpenAIClient(
        content='```json\n{"recommendations":[]}\n```'
    )

    result = NvidiaClient(
        settings,
        openai_client=openai_client,
        timeout=9,
    ).chat_json(
        system_prompt="system",
        user_prompt="user",
        response_schema={"type": "object"},
    )

    assert result == {"recommendations": []}


def test_nvidia_client_invalid_json_error_includes_excerpt(tmp_path):
    db_file = tmp_path / "db.sqlite3"
    db_file.touch()
    settings = AppConfig(
        sqlite_db_path=db_file,
        ollama_model="",
        ollama_base_url="",
        api_usage="NVIDIA",
        nvidia_api_model="nvidia/llama-3.1-nemotron",
        nvidia_api_key="secret-key",
    )
    openai_client = FakeOpenAIClient(content="I cannot return recommendations.")

    with pytest.raises(OllamaResponseError, match="Response excerpt"):
        NvidiaClient(
            settings,
            openai_client=openai_client,
            timeout=9,
        ).chat_json(
            system_prompt="system",
            user_prompt="user",
            response_schema={"type": "object"},
        )


def test_chat_client_factory_uses_nvidia_when_configured(tmp_path):
    db_file = tmp_path / "db.sqlite3"
    db_file.touch()
    settings = AppConfig(
        sqlite_db_path=db_file,
        ollama_model="",
        ollama_base_url="",
        api_usage="NVIDIA",
        nvidia_api_model="nvidia/model",
        nvidia_api_key="secret-key",
    )

    assert isinstance(create_chat_client(settings), NvidiaClient)


def test_nvidia_client_can_disable_ssl_verification(tmp_path):
    db_file = tmp_path / "db.sqlite3"
    db_file.touch()
    settings = AppConfig(
        sqlite_db_path=db_file,
        ollama_model="",
        ollama_base_url="",
        api_usage="NVIDIA",
        nvidia_api_model="nvidia/model",
        nvidia_api_key="secret-key",
        nvidia_verify_ssl=False,
    )
    openai_client = FakeOpenAIClient(content="ok")

    result = NvidiaClient(
        settings,
        openai_client=openai_client,
        timeout=9,
    ).chat(
        system_prompt="system",
        user_prompt="user",
    )

    assert result == "ok"
    assert openai_client.completions.calls[0]["model"] == "nvidia/model"


def test_nvidia_client_constructs_openai_client_with_httpx_verify_false(
    tmp_path,
    monkeypatch,
):
    db_file = tmp_path / "db.sqlite3"
    db_file.touch()
    settings = AppConfig(
        sqlite_db_path=db_file,
        ollama_model="",
        ollama_base_url="",
        api_usage="NVIDIA",
        nvidia_api_model="nvidia/model",
        nvidia_api_key="secret-key",
        nvidia_verify_ssl=False,
    )
    httpx_calls = []
    openai_calls = []

    def fake_httpx_client(**kwargs):
        httpx_calls.append(kwargs)
        return object()

    def fake_openai(**kwargs):
        openai_calls.append(kwargs)
        return FakeOpenAIClient(content="ok")

    monkeypatch.setattr(llm_client_module.httpx, "Client", fake_httpx_client)
    monkeypatch.setattr(llm_client_module, "OpenAI", fake_openai)

    result = NvidiaClient(settings, timeout=9).chat(
        system_prompt="system",
        user_prompt="user",
    )

    assert result == "ok"
    assert httpx_calls == [{"verify": False, "timeout": 9}]
    assert openai_calls[0]["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert openai_calls[0]["api_key"] == "secret-key"
    assert openai_calls[0]["timeout"] == 9


def test_nvidia_client_404_mentions_model_access_or_slug(tmp_path):
    db_file = tmp_path / "db.sqlite3"
    db_file.touch()
    settings = AppConfig(
        sqlite_db_path=db_file,
        ollama_model="",
        ollama_base_url="",
        api_usage="NVIDIA",
        nvidia_api_model="google/gemma-7b",
        nvidia_api_key="secret-key",
    )
    openai_client = FakeOpenAIClient(
        error=FakeOpenAINotFoundError("404 Client Error: Not Found")
    )

    with pytest.raises(OllamaError) as exc_info:
        NvidiaClient(
            settings,
            openai_client=openai_client,
            timeout=9,
        ).chat_json(
            system_prompt="system",
            user_prompt="user",
            response_schema={"type": "object"},
        )

    assert "model slug is not available" in str(exc_info.value)


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
        portfolio_strategy_recommendation="Use a low-cost balanced core.",
    )
    payload = json.loads(prompt)
    assert payload["target_asset_class_weight"] == "1.0000000000"
    assert payload["held_security_ids"] == [11]
    assert payload["portfolio_strategy_recommendation"] == (
        "Use a low-cost balanced core."
    )
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
        "strategy_allocation_signals",
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
        signal = connection.execute(
            text("SELECT * FROM strategy_allocation_signals WHERE run_id = :id"),
            {"id": run_id},
        ).mappings().one()

    assert run["llm_model_used"] == "gemma4"
    assert run["llm_overall_rationale"].startswith("Positive trend")
    assert Decimal(str(target["target_weight"])) == Decimal("1")
    assert signal["run_id"] == run_id
    assert signal["signal_timestamp"] is not None
    assert signal["signal_scope"] == "ASSET_CLASS"
    assert signal["asset_class_code"] == "ETF"
    assert signal["security_id"] is None
    assert signal["ticker"] is None
    assert signal["trend_positive"] == 1
    assert signal["momentum_12m"] == 0.24
    assert signal["annualized_volatility"] == 0.13
    assert signal["security_count"] == 2
    assert recommendation["action_type"] == "BUY"
    assert Decimal(str(recommendation["estimated_trade_value"])) == Decimal("0")
    assert recommendation["is_executed"] == 0


def test_pipeline_normalizes_tiny_recommendation_weight_residual(
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
                    "security_id": 10,
                    "ticker": "LOWFEE",
                    "action_type": "BUY",
                    "target_weight": 0.9999,
                    "rationale": "Close enough after model rounding.",
                }
            ]
        },
    )
    pipeline = AdvisoryPipeline(client, StrategyRepository(advisory_engine))
    run_id = pipeline.rationalize_allocation(portfolio_id=7, allocation=allocation)

    recommendations = pipeline.screen_asset_class(
        run_id=run_id,
        portfolio_id=7,
        asset_class_code="ETF",
        target_weight=Decimal("1.0"),
        candidate_context={
            "securities": [{"security_id": 10, "ticker": "LOWFEE"}]
        },
    )

    assert recommendations[0].target_weight == Decimal("1.0000")
    with advisory_engine.connect() as connection:
        target_weight = connection.execute(
            text(
                "SELECT target_weight FROM asset_recommendations WHERE run_id = :id"
            ),
            {"id": run_id},
        ).scalar_one()
    assert Decimal(str(target_weight)) == Decimal("1.0000")


def test_pipeline_rejects_material_recommendation_weight_mismatch(
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
                    "security_id": 10,
                    "ticker": "LOWFEE",
                    "action_type": "BUY",
                    "target_weight": 0.95,
                    "rationale": "Wrong total.",
                }
            ]
        },
    )
    pipeline = AdvisoryPipeline(client, StrategyRepository(advisory_engine))
    run_id = pipeline.rationalize_allocation(portfolio_id=7, allocation=allocation)

    with pytest.raises(OllamaResponseError, match=r"sum=0.95, target=1.0"):
        pipeline.screen_asset_class(
            run_id=run_id,
            portfolio_id=7,
            asset_class_code="ETF",
            target_weight=Decimal("1.0"),
            candidate_context={
                "securities": [{"security_id": 10, "ticker": "LOWFEE"}]
            },
        )


def test_pipeline_repairs_all_zero_recommendation_weight_for_positive_target(
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
                    "security_id": 10,
                    "ticker": "LOWFEE",
                    "action_type": "HOLD",
                    "target_weight": 0,
                    "rationale": "Best candidate but model emitted zero.",
                }
            ]
        },
    )
    pipeline = AdvisoryPipeline(client, StrategyRepository(advisory_engine))
    run_id = pipeline.rationalize_allocation(portfolio_id=7, allocation=allocation)

    recommendations = pipeline.screen_asset_class(
        run_id=run_id,
        portfolio_id=7,
        asset_class_code="ETF",
        target_weight=Decimal("1.0"),
        candidate_context={"securities": [{"security_id": 10, "ticker": "LOWFEE"}]},
    )

    assert recommendations[0].target_weight == Decimal("1.0")


def test_repository_persists_security_signal_calculations(
    advisory_engine,
    tmp_path,
):
    allocation = AllocationResult(
        model="HIERARCHICAL",
        as_of=date(2026, 6, 25),
        weights={"ETF": Decimal("1.0000000000")},
        signals=(
            AssetClassSignal(
                asset_class_code="ETF",
                trend_positive=True,
                momentum_12m=0.24,
                annualized_volatility=0.13,
                security_count=1,
            ),
        ),
        security_signals=(
            SecuritySignal(
                security_id=10,
                ticker="LOWFEE",
                asset_class_code="ETF",
                trend_positive=True,
                momentum_12m=0.31,
                annualized_volatility=0.09,
            ),
        ),
    )
    settings = AppConfig(
        sqlite_db_path=tmp_path / "unused.sqlite3",
        ollama_model="gemma4",
        ollama_base_url="http://localhost:11434",
    )
    client = FakeOllamaClient(
        settings,
        rationale="Rationale",
        screening_response={"recommendations": []},
    )
    repository = StrategyRepository(advisory_engine)
    pipeline = AdvisoryPipeline(client, repository)

    run_id = pipeline.rationalize_allocation(
        portfolio_id=7,
        allocation=allocation,
    )

    with advisory_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT *
                FROM strategy_allocation_signals
                WHERE run_id = :run_id
                ORDER BY signal_scope
                """
            ),
            {"run_id": run_id},
        ).mappings().all()

    assert [row["signal_scope"] for row in rows] == ["ASSET_CLASS", "SECURITY"]
    security_row = rows[1]
    assert security_row["signal_timestamp"] is not None
    assert security_row["security_id"] == 10
    assert security_row["ticker"] == "LOWFEE"
    assert security_row["asset_class_code"] == "ETF"
    assert security_row["trend_positive"] == 1
    assert security_row["momentum_12m"] == 0.31
    assert security_row["annualized_volatility"] == 0.09
    assert security_row["security_count"] == 1


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
