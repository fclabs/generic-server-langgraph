"""Integration: LCEL chain with LangChain FakeListLLM behind create_runnable_app HTTP routes."""

from __future__ import annotations

import json
from typing import cast

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import FakeListLLM
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda

from langgraph_runnable_server import create_runnable_app


@pytest.mark.integration
def test_api_invokes_real_chain_with_fake_list_llm() -> None:
    """
    Given a served runnable that is a real prompt-to-LLM chain backed by LangChain's
    deterministic fake list model,
    When a client invokes that runnable with structured input,
    Then the JSON response is the fake model's canned completion string.
    """
    # Given
    prompt = PromptTemplate.from_template("Question: {question}\nAnswer:")
    llm = FakeListLLM(responses=["synthetic completion"])
    chain = cast(Runnable, prompt | llm)
    app = create_runnable_app(prefix="/agents", runnables={"agent1": chain})
    # When
    with TestClient(app) as client:
        res = client.post(
            "/agents/agent1/invoke",
            json={"input": {"question": "what is 2+2?"}},
        )
    # Then
    assert res.status_code == 200
    assert res.json() == "synthetic completion"


@pytest.mark.integration
def test_api_batches_real_chain_with_fake_list_llm() -> None:
    """
    Given the same kind of chain with two canned answers,
    When a client batches two distinct inputs,
    Then the response is a JSON array of the two answers in order.
    """
    # Given
    prompt = PromptTemplate.from_template("Topic: {topic}")
    llm = FakeListLLM(responses=["alpha", "beta"])
    chain = cast(Runnable, prompt | llm)
    app = create_runnable_app(prefix="/agents", runnables={"agent1": chain})
    # When
    with TestClient(app) as client:
        res = client.post(
            "/agents/agent1/batch",
            json={"inputs": [{"topic": "one"}, {"topic": "two"}]},
        )
    # Then
    assert res.status_code == 200
    assert res.json() == ["alpha", "beta"]


@pytest.mark.integration
def test_api_invoke_and_batch_return_json_objects_from_structured_fake_llm_chain() -> None:
    """
    Given chains where the fake LLM emits JSON text and a runnable step parses that into
    dictionaries (nested structures),
    When clients call invoke and batch,
    Then the HTTP bodies are a single JSON object and a JSON array of objects respectively,
    matching what FastAPI's jsonable_encoder would produce for those dicts.
    """
    # Given (invoke) — JSON object root from parsed fake LLM output
    prompt = PromptTemplate.from_template("Q: {q}")
    llm_invoke = FakeListLLM(
        responses=['{"answer": "invoke-ok", "meta": {"confidence_pct": 99}}'],
    )
    chain_invoke = cast(Runnable, prompt | llm_invoke | RunnableLambda(json.loads))
    app_invoke = create_runnable_app(prefix="/agents", runnables={"agent1": chain_invoke})
    # When (invoke)
    with TestClient(app_invoke) as client:
        inv = client.post("/agents/agent1/invoke", json={"input": {"q": "x"}})
    # Then (invoke)
    assert inv.status_code == 200
    assert inv.headers.get("content-type", "").startswith("application/json")
    assert inv.json() == {"answer": "invoke-ok", "meta": {"confidence_pct": 99}}

    # Given (batch) — fresh fake LLM so response indices align with two batch inputs
    llm_batch = FakeListLLM(
        responses=[
            '{"answer": "first", "items": ["a"]}',
            '{"answer": "second", "items": ["b", "c"]}',
        ],
    )
    chain_batch = cast(Runnable, prompt | llm_batch | RunnableLambda(json.loads))
    app_batch = create_runnable_app(prefix="/agents", runnables={"agent1": chain_batch})
    # When (batch)
    with TestClient(app_batch) as client:
        bat = client.post(
            "/agents/agent1/batch",
            json={"inputs": [{"q": "1"}, {"q": "2"}]},
        )
    # Then (batch)
    assert bat.status_code == 200
    assert bat.headers.get("content-type", "").startswith("application/json")
    assert bat.json() == [
        {"answer": "first", "items": ["a"]},
        {"answer": "second", "items": ["b", "c"]},
    ]
