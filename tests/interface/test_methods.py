"""HTTP method behavior on probe paths (VC-022)."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from langgraph_runnable_server import create_app

_NON_GET = ("HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


@pytest.mark.parametrize(
    ("make_app", "health_path", "metrics_path"),
    [
        (partial(create_app), "/health", "/metrics"),
        (partial(create_app, prefix="/api"), "/api/health", "/api/metrics"),
    ],
)
@pytest.mark.parametrize("method", _NON_GET)
def test_vc022_non_get_on_probe_paths_returns_404(
    make_app: Callable[[], FastAPI],
    health_path: str,
    metrics_path: str,
    method: str,
) -> None:
    """Given probe URLs, when a non-GET method is used, then the response is 404 (not 405)."""
    # Given
    app = make_app()
    # When / Then
    with TestClient(app) as client:
        for path in (health_path, metrics_path):
            response = client.request(method, path)
            assert response.status_code == 404, (method, path, response.status_code)


@pytest.mark.parametrize(
    ("make_app", "health_path", "metrics_path"),
    [
        (partial(create_app), "/health", "/metrics"),
        (partial(create_app, prefix="/api"), "/api/health", "/api/metrics"),
    ],
)
def test_vc022_get_on_probe_paths_still_200(
    make_app: Callable[[], FastAPI],
    health_path: str,
    metrics_path: str,
) -> None:
    """Given probe URLs, when GET is used, then health and metrics behave as before."""
    # Given
    app = make_app()
    # When
    with TestClient(app) as client:
        health = client.get(health_path)
        metrics = client.get(metrics_path)
    # Then
    assert health.status_code == 200
    assert health.content == b"ok"
    assert health.headers["content-type"].startswith("text/plain")
    assert metrics.status_code == 200
    assert metrics.content == b""
    # Clean (TestClient context exited)
