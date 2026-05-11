"""Interface tests for health, metrics, instance_id, lifespan, and I/O guards (VC-001–VC-010)."""

from __future__ import annotations

import inspect
import socket
import urllib.request
from collections.abc import Callable
from contextlib import ExitStack
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from langgraph_runnable_server import create_app
from langgraph_runnable_server.metrics import registry


def _health_route_on_stack(stack: list[inspect.FrameInfo]) -> bool:
    for frame in stack:
        path = frame.filename.replace("\\", "/")
        if "langgraph_runnable_server/api/routes/health.py" in path:
            return True
    return False


def _patch_network_guards(stack: ExitStack) -> None:
    real_socket_cls = socket.socket
    real_connect = real_socket_cls.connect
    real_connect_ex = real_socket_cls.connect_ex
    real_client_send = httpx.Client.send
    real_async_send = httpx.AsyncClient.send
    real_urlopen = urllib.request.urlopen

    def guarded_socket(*args, **kwargs):
        if _health_route_on_stack(inspect.stack()):
            raise AssertionError("health must not perform external I/O: socket.socket")
        return real_socket_cls(*args, **kwargs)

    def guarded_connect(self, *args, **kwargs):
        if _health_route_on_stack(inspect.stack()):
            raise AssertionError("health must not perform external I/O: socket.socket.connect")
        return real_connect(self, *args, **kwargs)

    def guarded_connect_ex(self, *args, **kwargs):
        if _health_route_on_stack(inspect.stack()):
            raise AssertionError("health must not perform external I/O: socket.socket.connect_ex")
        return real_connect_ex(self, *args, **kwargs)

    def guarded_client_send(self, request, *args, **kwargs):
        if _health_route_on_stack(inspect.stack()):
            raise AssertionError("health must not perform external I/O: httpx.Client.send")
        return real_client_send(self, request, *args, **kwargs)

    def guarded_async_send(self, request, *args, **kwargs):
        if _health_route_on_stack(inspect.stack()):
            raise AssertionError("health must not perform external I/O: httpx.AsyncClient.send")
        return real_async_send(self, request, *args, **kwargs)

    def guarded_urlopen(*args, **kwargs):
        if _health_route_on_stack(inspect.stack()):
            raise AssertionError("health must not perform external I/O: urllib.request.urlopen")
        return real_urlopen(*args, **kwargs)

    stack.enter_context(patch("socket.socket", guarded_socket))
    stack.enter_context(patch.object(real_socket_cls, "connect", guarded_connect))
    stack.enter_context(patch.object(real_socket_cls, "connect_ex", guarded_connect_ex))
    stack.enter_context(patch.object(httpx.Client, "send", guarded_client_send))
    stack.enter_context(patch.object(httpx.AsyncClient, "send", guarded_async_send))
    stack.enter_context(patch("urllib.request.urlopen", guarded_urlopen))


def test_vc001_instance_id_present() -> None:
    """Given a new app, when reading state, then instance_id is a non-empty string."""
    # Given
    # When
    app = create_app()
    # Then
    instance_id = app.state["instance_id"]
    assert isinstance(instance_id, str)
    assert len(instance_id) > 0
    assert len(instance_id) == 36
    assert instance_id.count("-") == 4
    assert app.state.instance_id == instance_id


def test_vc002_instance_ids_unique_per_app() -> None:
    """Given two apps from the factory, when comparing state, then instance_ids differ."""
    # Given
    # When
    a = create_app()
    b = create_app()
    # Then
    assert a.state["instance_id"] != b.state["instance_id"]


def test_vc003a_default_lifespan_registered() -> None:
    """Given the default app, when using TestClient context, then lifespan exits cleanly."""
    # Given
    app = create_app()
    # Then
    assert app.router.lifespan_context is not None
    # When / Then (no exception)
    with TestClient(app):
        pass


def test_vc004_default_health_endpoint() -> None:
    """Given the default app, when GET /health, then 200, ok body, and text/plain."""
    # Given
    client = TestClient(create_app())
    # When
    response = client.get("/health")
    # Then
    assert response.status_code == 200
    assert response.content == b"ok"
    assert response.headers.get("content-type", "").startswith("text/plain")


def test_vc005_default_metrics_endpoint() -> None:
    """Given the default app, when GET /metrics, then the body is empty and status is 200."""
    # Given
    client = TestClient(create_app())
    # When
    response = client.get("/metrics")
    # Then
    assert response.status_code == 200
    assert response.content == b""


def test_vc004_prefixed_health_endpoint() -> None:
    """Given a prefixed app, when GET {base}/health, then 200, ok body, and text/plain."""
    # Given
    client = TestClient(create_app(prefix="/api"))
    # When
    response = client.get("/api/health")
    # Then
    assert response.status_code == 200
    assert response.content == b"ok"
    assert response.headers.get("content-type", "").startswith("text/plain")


def test_vc005_prefixed_metrics_endpoint() -> None:
    """Given a prefixed app, when GET {base}/metrics, then 200 and empty body."""
    # Given
    client = TestClient(create_app(prefix="/api"))
    # When
    response = client.get("/api/metrics")
    # Then
    assert response.status_code == 200
    assert response.content == b""


def test_vc008_health_body_byte_exact_ok_literal() -> None:
    """Given the default app, when GET /health, then body is ASCII ok with no suffix (BR-001)."""
    # Given
    client = TestClient(create_app())
    # When
    response = client.get("/health")
    # Then
    assert response.content == b"ok"
    assert len(response.content) == 2


def test_vc009_health_does_not_invoke_network_primitives_from_route() -> None:
    """Given guarded network primitives, when GET /health, then the route does not trigger them."""
    # Given
    app = create_app()
    client = TestClient(app)
    with ExitStack() as stack:
        _patch_network_guards(stack)
        # When
        response = client.get("/health")
    # Then
    assert response.status_code == 200
    assert response.content == b"ok"


def test_vc009_prefixed_health_does_not_invoke_network_primitives_from_route() -> None:
    """Given guarded network primitives, when GET {base}/health on a prefixed app, then clean."""
    # Given
    app = create_app(prefix="/api")
    client = TestClient(app)
    with ExitStack() as stack:
        _patch_network_guards(stack)
        # When
        response = client.get("/api/health")
    # Then
    assert response.status_code == 200
    assert response.content == b"ok"


def test_vc010_metrics_registry_empty() -> None:
    """Given the metrics registry module, when reading METRICS, then the registry is empty."""
    # Given / When / Then
    assert registry.METRICS == ()


@pytest.mark.parametrize(
    ("factory", "health_path", "metrics_path"),
    [
        (lambda: create_app(), "/health", "/metrics"),
        (lambda: create_app(prefix=""), "/health", "/metrics"),
        (lambda: create_app(prefix="   "), "/health", "/metrics"),
        (lambda: create_app(prefix="/api"), "/api/health", "/api/metrics"),
    ],
)
def test_vc017_prefix_maps_base_urls(
    factory: Callable[[], FastAPI],
    health_path: str,
    metrics_path: str,
) -> None:
    """Given factory variants, when probing resolved paths, then health and metrics match {base}."""
    # Given
    client = TestClient(factory())
    # When
    h = client.get(health_path)
    m = client.get(metrics_path)
    # Then
    assert h.status_code == 200
    assert h.content == b"ok"
    assert m.status_code == 200
    assert m.content == b""


def test_vc018_trailing_slash_on_prefix_normalizes_like_without() -> None:
    """Given /api/ vs /api, when GET probe paths, then both apps expose the same URLs."""
    # Given
    slash_client = TestClient(create_app(prefix="/api/"))
    plain_client = TestClient(create_app(prefix="/api"))
    # When / Then
    for client in (slash_client, plain_client):
        h = client.get("/api/health")
        m = client.get("/api/metrics")
        assert h.status_code == 200
        assert h.content == b"ok"
        assert m.status_code == 200
        assert m.content == b""


@pytest.mark.parametrize("bad_prefix", ["/api///", "/api//"])
def test_vc018_double_slash_in_prefix_rejected_before_normalization(bad_prefix: str) -> None:
    """Given a prefix containing //, when create_app, then ValueError and no FastAPI build."""
    # Given
    with patch("langgraph_runnable_server.app.FastAPI") as mock_fastapi:
        # When / Then
        with pytest.raises(ValueError):
            create_app(prefix=bad_prefix)
        mock_fastapi.assert_not_called()


@pytest.mark.parametrize(
    "bad_prefix",
    [
        "api",
        "//",
        "/api//v1",
        "/a b",
        "/a?b",
        "/a#b",
        "/a<b",
    ],
)
def test_vc019_invalid_prefix_raises_value_error_before_fastapi(bad_prefix: str) -> None:
    """Given invalid prefixes, when create_app, then ValueError and FastAPI is never built."""
    # Given
    with patch("langgraph_runnable_server.app.FastAPI") as mock_fastapi:
        # When / Then
        with pytest.raises(ValueError):
            create_app(prefix=bad_prefix)
        mock_fastapi.assert_not_called()
