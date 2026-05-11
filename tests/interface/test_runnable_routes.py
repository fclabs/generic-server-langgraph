"""Runnable POST routes: invoke/batch happy path, serialization, lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.runnables import Runnable
from pydantic import BaseModel
from runnable_stubs import StubRunnable

from langgraph_runnable_server import create_runnable_app

LIFESPAN_SENTINEL = object()


def test_vc101_partial_probes_and_runnable_routing() -> None:
    """Given two runnables under /agents, when POST targets one key, then only that stub runs."""
    # Given
    r_a = StubRunnable()
    r_b = StubRunnable()
    runnables = cast(dict[str, Runnable], {"a": r_a, "b": r_b})
    app = create_runnable_app(prefix="/agents", runnables=runnables)
    # When
    with TestClient(app) as client:
        health = client.get("/health")
        inv = client.post("/agents/a/invoke", json={"input": {"x": 1}})
    # Then
    assert health.status_code == 200
    assert health.content == b"ok"
    assert inv.status_code == 200
    assert inv.json() == {"echo": {"x": 1}}
    assert r_a.ainvoke_calls == [({"x": 1}, None)]
    assert r_b.ainvoke_calls == []


def test_vc102_path_layout_and_stream_404() -> None:
    """Given agent1 and agent2, when hitting invoke/batch paths, then they work; stream is 404."""
    # Given
    s1 = StubRunnable()
    s2 = StubRunnable()
    runnables = cast(dict[str, Runnable], {"agent1": s1, "agent2": s2})
    app = create_runnable_app(prefix="/agents", runnables=runnables)
    # When
    with TestClient(app) as client:
        i1 = client.post("/agents/agent1/invoke", json={"input": 1})
        b1 = client.post("/agents/agent1/batch", json={"inputs": [1, 2]})
        i2 = client.post("/agents/agent2/invoke", json={"input": 2})
        b2 = client.post("/agents/agent2/batch", json={"inputs": [3]})
        stream = client.post("/agents/agent1/stream", json={"input": 1})
    # Then
    assert i1.status_code == 200
    assert b1.status_code == 200
    assert i2.status_code == 200
    assert b2.status_code == 200
    assert stream.status_code == 404


def test_vc103_invoke_and_batch_pass_config() -> None:
    """Given config in JSON bodies, when invoking and batching, then stubs receive that config."""
    # Given
    stub = StubRunnable()
    runnables = cast(dict[str, Runnable], {"agent1": stub})
    app = create_runnable_app(prefix="/agents", runnables=runnables)
    cfg = {"tags": ["t"]}
    # When
    with TestClient(app) as client:
        inv = client.post("/agents/agent1/invoke", json={"input": {"x": 1}, "config": cfg})
        bat = client.post(
            "/agents/agent1/batch",
            json={"inputs": [{"x": 1}, {"x": 2}], "config": cfg},
        )
    # Then
    assert inv.status_code == 200
    assert bat.status_code == 200
    assert stub.ainvoke_calls[0] == ({"x": 1}, cfg)
    assert stub.abatch_calls[0] == ([{"x": 1}, {"x": 2}], cfg)
    assert bat.json() == [{"echo": {"x": 1}}, {"echo": {"x": 2}}]


@pytest.mark.parametrize("chosen", ["agent1", "agent2"])
def test_fr104_no_cross_routing(chosen: str) -> None:
    """Given two keys, when POST targets one, then only that runnable records the call."""
    # Given
    stubs = {"agent1": StubRunnable(), "agent2": StubRunnable()}
    app = create_runnable_app(prefix="/agents", runnables=cast(dict[str, Runnable], stubs))
    # When
    with TestClient(app) as client:
        client.post(f"/agents/{chosen}/invoke", json={"input": "only-one"})
    # Then
    for k, s in stubs.items():
        if k == chosen:
            assert s.ainvoke_calls == [("only-one", None)]
        else:
            assert s.ainvoke_calls == []


def test_br102_empty_batch_short_circuits_abatch() -> None:
    """Given empty inputs, when POST batch, then [] and abatch is not called."""
    # Given
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/batch", json={"inputs": []})
    # Then
    assert r.status_code == 200
    assert r.json() == []
    assert stub.abatch_calls == []


def test_null_input_invoke() -> None:
    """Given JSON null for input, when POST invoke, then runnable receives None."""
    # Given
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/invoke", json={"input": None})
    # Then
    assert r.status_code == 200
    assert stub.ainvoke_calls[0] == (None, None)


class _SmallModel(BaseModel):
    label: str


def test_br103_jsonable_encoder_pydantic() -> None:
    """Given a runnable returning a Pydantic model, when POST invoke, then JSON is plain fields."""
    # Given
    stub = StubRunnable(response=_SmallModel(label="hi"))
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/invoke", json={"input": {}})
    # Then
    assert r.status_code == 200
    assert r.json() == {"label": "hi"}
    body = r.text
    assert "lc" not in body


def test_br103_jsonable_encoder_datetime() -> None:
    """Given a datetime return value, when POST invoke, then ISO JSON is returned."""
    # Given
    dt = datetime(2024, 5, 1, 12, 30, 45, tzinfo=UTC)
    stub = StubRunnable(response=dt)
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/invoke", json={"input": {}})
    # Then
    assert r.status_code == 200
    assert r.json() == dt.isoformat()


def test_br103_jsonable_encoder_uuid() -> None:
    """Given a UUID return value, when POST invoke, then JSON is the string form."""
    # Given
    u = uuid4()
    stub = StubRunnable(response=u)
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/invoke", json={"input": {}})
    # Then
    assert r.status_code == 200
    assert r.json() == str(u)
    assert UUID(r.json()) == u


def test_br103_jsonable_encoder_set() -> None:
    """Given a set return value, when POST invoke, then JSON is an array without lc envelope."""
    # Given
    stub = StubRunnable(response={3, 1, 2})
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/invoke", json={"input": {}})
    # Then
    assert r.status_code == 200
    assert sorted(r.json()) == [1, 2, 3]
    assert "lc" not in r.text


def test_fr106_empty_runnables_unknown_invoke_404() -> None:
    """Given no runnables, when GET health and POST unknown invoke, then 200 and 404."""
    # Given
    app = create_runnable_app(prefix="/agents", runnables={})
    # When
    with TestClient(app) as client:
        health = client.get("/health")
        inv = client.post("/agents/foo/invoke", json={"input": {}})
    # Then
    assert health.status_code == 200
    assert inv.status_code == 404


def test_vc111_lifespan_passthrough_with_sentinel() -> None:
    """Given a host lifespan with a sentinel, when TestClient runs, it is unwrapped."""

    # Given
    @asynccontextmanager
    async def host_lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state["lifespan_sentinel"] = LIFESPAN_SENTINEL
        app.state.started = True
        yield
        app.state.shutdown = True

    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"a": stub}),
        lifespan=host_lifespan,
    )
    assert not hasattr(app.state, "started")
    # When
    with TestClient(app) as client:
        client.get("/health")
        # Then (inside context)
        assert app.state["lifespan_sentinel"] is LIFESPAN_SENTINEL
        assert app.state.started is True
        assert not getattr(app.state, "shutdown", False)
    # Then (after context)
    assert app.state.shutdown is True
    assert app.state["lifespan_sentinel"] is LIFESPAN_SENTINEL


def test_malformed_json_returns_422() -> None:
    """Given invalid JSON body, when POST invoke, then 422 is returned."""
    # Given
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        r = client.post(
            "/agents/agent1/invoke",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
    # Then
    assert r.status_code == 422
    assert stub.ainvoke_calls == []
