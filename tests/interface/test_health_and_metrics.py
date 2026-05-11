"""Interface tests for health, metrics, instance id via HTTP, lifespan, and I/O guards (VC-001–VC-010)."""

from __future__ import annotations

import inspect
import socket
import urllib.request
from collections.abc import Callable
from contextlib import ExitStack, asynccontextmanager
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from langgraph_runnable_server import create_app


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


def test_vc003a_default_lifespan_registered() -> None:
    """Given the default app, when using TestClient context, then lifespan exits cleanly."""
    # Given
    app = create_app()
    # Then
    assert app.router.lifespan_context is not None
    # When / Then (no exception)
    with TestClient(app):
        pass


def test_vc003b_host_supplied_lifespan_startup_and_shutdown() -> None:
    """Given a host lifespan, when TestClient runs, startup in ctx and shutdown after exit."""

    # Given
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state["startup_ran"] = True
        yield
        app.state["shutdown_ran"] = True

    app = create_app(lifespan=lifespan)
    # When / Then
    with TestClient(app) as client:
        client.get("/health")
        assert app.state["startup_ran"] is True
        assert not hasattr(app.state, "shutdown_ran")
    assert app.state["shutdown_ran"] is True


def test_host_lifespan_startup_error_propagates_from_test_client_enter() -> None:
    """Given lifespan raises on startup, when TestClient enters, then RuntimeError propagates."""

    # Given
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        raise RuntimeError("lifespan startup failed")
        yield  # pragma: no cover

    app = create_app(lifespan=lifespan)
    # When / Then
    with pytest.raises(RuntimeError, match="lifespan startup failed"):
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
