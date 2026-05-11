"""Runnable request-body validation: Content-Type, JSON shape, invoke/batch fields."""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.runnables import Runnable
from runnable_stubs import StubRunnable

from langgraph_runnable_server import create_runnable_app


def _app() -> tuple[StubRunnable, FastAPI]:
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    return stub, app


def test_invoke_empty_object_422() -> None:
    """Given POST invoke with {}, when handled, then 422 with detail."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/invoke", json={})
    # Then
    assert r.status_code == 422
    assert "detail" in r.json()
    assert stub.ainvoke_calls == []


def test_invoke_null_input_200() -> None:
    """Given input null, when POST invoke, then 200 and runnable receives None."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/invoke", json={"input": None})
    # Then
    assert r.status_code == 200
    assert stub.ainvoke_calls == [(None, None)]


def test_invoke_missing_input_key_422() -> None:
    """Given body without input, when POST invoke, then 422."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/invoke", json={"foo": 1})
    # Then
    assert r.status_code == 422
    assert "detail" in r.json()
    assert stub.ainvoke_calls == []


def test_invoke_root_json_array_422() -> None:
    """Given JSON array root, when POST invoke, then 422."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post(
            "/agents/agent1/invoke",
            content=b"[1, 2, 3]",
            headers={"Content-Type": "application/json"},
        )
    # Then
    assert r.status_code == 422
    assert "detail" in r.json()
    assert stub.ainvoke_calls == []


def test_invoke_root_json_number_422() -> None:
    """Given JSON number root, when POST invoke, then 422."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post(
            "/agents/agent1/invoke",
            content=b"42",
            headers={"Content-Type": "application/json"},
        )
    # Then
    assert r.status_code == 422
    assert "detail" in r.json()
    assert stub.ainvoke_calls == []


def test_invoke_root_json_string_422() -> None:
    """Given JSON string root, when POST invoke, then 422."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post(
            "/agents/agent1/invoke",
            content=b'"hello"',
            headers={"Content-Type": "application/json"},
        )
    # Then
    assert r.status_code == 422
    assert "detail" in r.json()
    assert stub.ainvoke_calls == []


def test_invoke_root_json_bool_422() -> None:
    """Given JSON boolean root, when POST invoke, then 422."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post(
            "/agents/agent1/invoke",
            content=b"true",
            headers={"Content-Type": "application/json"},
        )
    # Then
    assert r.status_code == 422
    assert "detail" in r.json()
    assert stub.ainvoke_calls == []


def test_invoke_root_json_null_422() -> None:
    """Given JSON null root, when POST invoke, then 422."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post(
            "/agents/agent1/invoke",
            content=b"null",
            headers={"Content-Type": "application/json"},
        )
    # Then
    assert r.status_code == 422
    assert "detail" in r.json()
    assert stub.ainvoke_calls == []


def test_invoke_non_empty_body_missing_content_type_422() -> None:
    """Given raw body without Content-Type, when POST invoke, then 422 envelope."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post(
            "/agents/agent1/invoke",
            content=b'{"input": 1}',
            headers={},
        )
    # Then
    assert r.status_code == 422
    assert r.json() == {"detail": "Content-Type must be application/json"}
    assert stub.ainvoke_calls == []


def test_invoke_wrong_content_type_media_type_422() -> None:
    """Given text/plain Content-Type, when POST invoke, then 422."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post(
            "/agents/agent1/invoke",
            content=b'{"input": 1}',
            headers={"Content-Type": "text/plain"},
        )
    # Then
    assert r.status_code == 422
    assert r.json() == {"detail": "Content-Type must be application/json"}
    assert stub.ainvoke_calls == []


def test_invoke_malformed_json_with_json_content_type_422() -> None:
    """Given invalid JSON with application/json, when POST invoke, then 422 with detail string."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post(
            "/agents/agent1/invoke",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
    # Then
    assert r.status_code == 422
    body = r.json()
    assert isinstance(body["detail"], str)
    assert stub.ainvoke_calls == []


def test_batch_missing_inputs_422() -> None:
    """Given batch without inputs key, when POST, then 422."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/batch", json={})
    # Then
    assert r.status_code == 422
    assert "detail" in r.json()
    assert stub.abatch_calls == []


def test_batch_inputs_not_list_422() -> None:
    """Given inputs not a list, when POST batch, then 422."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/batch", json={"inputs": "not a list"})
    # Then
    assert r.status_code == 422
    assert "detail" in r.json()
    assert stub.abatch_calls == []


def test_batch_empty_inputs_200_regression() -> None:
    """Given empty inputs list, when POST batch, then 200 and abatch not called."""
    # Given
    stub, app = _app()
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/batch", json={"inputs": []})
    # Then
    assert r.status_code == 200
    assert r.json() == []
    assert stub.abatch_calls == []
