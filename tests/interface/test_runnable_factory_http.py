"""HTTP contract tests for create_runnable_app wiring (probes and empty runnables)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from langgraph_runnable_server import create_runnable_app
from runnable_stubs import StubRunnable


@pytest.fixture
def stub() -> StubRunnable:
    return StubRunnable()


def test_fr112_create_app_prefix_forwarding(stub: StubRunnable) -> None:
    """Given create_app_prefix='/api', when GET probe paths, then health is only under /api."""
    # Given
    app = create_runnable_app(prefix="/agents", runnables={}, create_app_prefix="/api")
    # When
    with TestClient(app) as client:
        ok = client.get("/api/health")
        miss = client.get("/health")
    # Then
    assert ok.status_code == 200
    assert ok.content == b"ok"
    assert miss.status_code == 404


def test_empty_runnables_probe_only(stub: StubRunnable) -> None:
    """Given empty runnables, when GET /health and POST invoke for unknown key, then 200 and 404."""
    # Given
    app = create_runnable_app(prefix="/agents", runnables={})
    # When
    with TestClient(app) as client:
        health = client.get("/health")
        invoke = client.post("/agents/foo/invoke", json={"input": {}})
    # Then
    assert health.status_code == 200
    assert health.content == b"ok"
    assert invoke.status_code == 404
