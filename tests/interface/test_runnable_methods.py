"""Runnable HTTP method discipline and unknown-key routing."""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.runnables import Runnable
from runnable_stubs import StubRunnable

from langgraph_runnable_server import create_runnable_app


def _app() -> FastAPI:
    return create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": StubRunnable()}),
    )


def test_get_invoke_405() -> None:
    """Given registered invoke path, when GET, then 405."""
    # Given
    app = _app()
    # When
    with TestClient(app) as client:
        r = client.get("/agents/agent1/invoke")
    # Then
    assert r.status_code == 405


def test_put_invoke_405() -> None:
    """Given registered invoke path, when PUT, then 405."""
    # Given
    app = _app()
    # When
    with TestClient(app) as client:
        r = client.put("/agents/agent1/invoke", json={"input": {}})
    # Then
    assert r.status_code == 405


def test_delete_invoke_405() -> None:
    """Given registered invoke path, when DELETE, then 405."""
    # Given
    app = _app()
    # When
    with TestClient(app) as client:
        r = client.delete("/agents/agent1/invoke")
    # Then
    assert r.status_code == 405


def test_post_unknown_key_invoke_404() -> None:
    """Given unknown runnable key, when POST invoke, then 404."""
    # Given
    app = _app()
    # When
    with TestClient(app) as client:
        r = client.post("/agents/no_such_key/invoke", json={"input": {}})
    # Then
    assert r.status_code == 404


def test_get_batch_405() -> None:
    """Given registered batch path, when GET, then 405."""
    # Given
    app = _app()
    # When
    with TestClient(app) as client:
        r = client.get("/agents/agent1/batch")
    # Then
    assert r.status_code == 405
