"""Runnable server errors: 500 envelope, no traceback leak to client."""

from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient
from langchain_core.runnables import Runnable
from runnable_stubs import StubRunnable

from langgraph_runnable_server import create_runnable_app


def test_invoke_runtime_error_500_envelope_no_traceback() -> None:
    """Given runnable raises RuntimeError, when POST invoke, then 500 JSON detail, no traceback."""
    # Given
    stub = StubRunnable(raise_on_invoke=RuntimeError("boom"))
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/invoke", json={"input": {}})
    # Then
    assert r.status_code == 500
    text = r.text
    assert "Traceback" not in text
    assert 'File "' not in text
    assert "langgraph_runnable_server" not in text
    assert r.json() == {"detail": "boom"}
