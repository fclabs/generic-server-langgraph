"""Structlog wide events (iteration 5): VC-109, VC-110, NFR-107, VC-118, BR-301, BR-108.

``http.route`` uses the literal registered path per runnable key (e.g. ``/agents/agent1/invoke``),
not a parameterized ``/agents/{key}/invoke`` — see runnable_app route-template note (VC-109 vs
BR-301 example wording).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.runnables import Runnable
from runnable_stubs import StubRunnable
from structlog.testing import capture_logs

from langgraph_runnable_server import create_runnable_app


def _http_request_events(entries: list) -> list:
    return [e for e in entries if e.get("event") == "http_request"]


@pytest.fixture
def http_request_log_capture() -> Iterator[list[Any]]:
    """Given structlog test processors, when tests log, then events append to the yielded list."""
    with capture_logs() as cap:
        yield cap


def _non_harness_access_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    """Filter caplog noise from pytest/pluggy while keeping uvicorn.access / fastapi names."""
    skip = ("pytest", "_pytest", "pluggy")
    out: list[logging.LogRecord] = []
    for r in records:
        if r.name not in ("uvicorn.access", "fastapi"):
            continue
        pn = getattr(r, "pathname", "") or ""
        if any(s in pn for s in skip):
            continue
        out.append(r)
    return out


def test_vc109_five_mixed_requests_emit_five_wide_events_with_br301_fields(
    http_request_log_capture: list[Any],
) -> None:
    """Given mixed probe and runnable traffic, when five requests run, then five BR-301 events."""

    # Given
    stub = StubRunnable()
    runnables = cast(dict[str, Runnable], {"agent1": stub})
    app = create_runnable_app(prefix="/agents", runnables=runnables)
    # When
    with TestClient(app) as client:
        iid = cast(FastAPI, client.app).state.instance_id
        client.get("/health")
        client.get("/metrics")
        client.post("/agents/agent1/invoke", json={"input": 1})
        client.post("/agents/agent1/invoke", json={"input": 2})
        client.post("/agents/agent1/batch", json={})
    # Then
    events = _http_request_events(http_request_log_capture)
    assert len(events) == 5
    for e in events:
        assert isinstance(e["duration_ms"], float)
        assert e["instance_id"] == iid
        assert "response_size_bytes" in e
        assert isinstance(e["response_size_bytes"], int)
        assert "http.method" in e
        assert "http.route" in e
        assert "http.status_code" in e
        assert "request_size_bytes" in e
        assert isinstance(e["request_size_bytes"], int)
    health = [e for e in events if e["http.route"] == "/health"]
    metrics = [e for e in events if e["http.route"] == "/metrics"]
    invokes = [e for e in events if e["http.route"] == "/agents/agent1/invoke"]
    batches = [e for e in events if e["http.route"] == "/agents/agent1/batch"]
    assert len(health) == 1
    assert health[0]["http.method"] == "GET"
    assert "runnable" not in health[0]
    assert "endpoint" not in health[0]
    assert len(metrics) == 1
    assert metrics[0]["http.method"] == "GET"
    assert "runnable" not in metrics[0]
    assert "endpoint" not in metrics[0]
    assert len(invokes) == 2
    assert all(x["runnable"] == "agent1" and x["endpoint"] == "invoke" for x in invokes)
    assert len(batches) == 1
    bat = batches[0]
    assert bat["runnable"] == "agent1"
    assert bat["endpoint"] == "batch"
    assert bat["http.status_code"] == 422
    # Clean
    assert stub.abatch_calls == []


def test_vc109_chunked_post_omits_request_size_bytes_in_wide_event(
    http_request_log_capture: list[Any],
) -> None:
    """Given chunked invoke without length, when 422 returns, then log omits request_size_bytes."""

    # Given
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )

    async def bad_chunks() -> AsyncIterator[bytes]:
        yield b'{"foo": 1}'

    async def run_chunked() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/agents/agent1/invoke",
                headers={"Content-Type": "application/json"},
                content=bad_chunks(),
            )
            assert r.status_code == 422

    # When
    asyncio.run(run_chunked())
    # Then
    events = _http_request_events(http_request_log_capture)
    assert len(events) == 1
    ev = events[0]
    assert "request_size_bytes" not in ev
    assert "response_size_bytes" in ev
    # Clean


def test_vc110_no_stdlib_access_records_beyond_harness_noise(
    http_request_log_capture: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Given caplog on access loggers, when five TestClient requests run, then no extra lines."""

    # Given
    caplog.set_level(logging.INFO)
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        client.get("/health")
        client.get("/metrics")
        client.post("/agents/agent1/invoke", json={"input": 1})
        client.post("/agents/agent1/invoke", json={"input": 2})
        client.post("/agents/agent1/batch", json={})
    # Then
    assert len(_http_request_events(http_request_log_capture)) == 5
    assert _non_harness_access_records(caplog.records) == []
    # Clean


def test_nfr107_hundred_health_requests_emit_hundred_events(
    http_request_log_capture: list[Any],
) -> None:
    """Given 100 sequential GET /health, when each returns 200, then exactly 100 wide events."""

    # Given
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"a": stub}),
    )
    # When
    with TestClient(app) as client:
        for _ in range(100):
            assert client.get("/health").status_code == 200
    # Then
    events = _http_request_events(http_request_log_capture)
    assert len(events) == 100
    assert all(e["http.route"] == "/health" for e in events)
    # Clean


def test_vc118_500_body_clean_and_wide_event_has_error_stack(
    http_request_log_capture: list[Any],
) -> None:
    """Given RuntimeError in stub, when invoke returns 500, then body is clean and log has stack."""

    # Given
    stub = StubRunnable(raise_on_invoke=RuntimeError("boom"))
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        resp = client.post("/agents/agent1/invoke", json={"input": 1})
    # Then
    assert resp.status_code == 500
    text = resp.text
    assert "Traceback" not in text
    assert 'File "' not in text
    events = _http_request_events(http_request_log_capture)
    inv_events = [e for e in events if e.get("http.route") == "/agents/agent1/invoke"]
    assert len(inv_events) == 1
    ev = inv_events[0]
    assert ev["error.type"] == "RuntimeError"
    assert "Traceback" in ev["error.stack"]
    # Clean


@pytest.mark.parametrize(
    ("traceparent", "expect_tid"),
    [
        (None, None),
        (
            "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
            "0123456789abcdef0123456789abcdef",
        ),
        ("malformed", None),
        (
            "00-ABCDEF0123456789ABCDEF0123456789-0123456789abcdef-01",
            None,
        ),
    ],
)
def test_br301_trace_id_from_traceparent_header(
    http_request_log_capture: list[Any],
    traceparent: str | None,
    expect_tid: str | None,
) -> None:
    """Given traceparent variants, when GET /health runs, then trace_id follows W3C hex rule."""

    # Given
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"a": StubRunnable()}),
    )
    headers = {}
    if traceparent is not None:
        headers["traceparent"] = traceparent
    # When
    with TestClient(app) as client:
        client.get("/health", headers=headers)
    # Then
    ev = _http_request_events(http_request_log_capture)[0]
    if expect_tid is None:
        assert "trace_id" not in ev
    else:
        assert ev["trace_id"] == expect_tid
    # Clean


class _SlowRunnable:
    """Sleeps in ``ainvoke`` until cancelled."""

    def __init__(self) -> None:
        self.cancelled_seen = False

    async def ainvoke(self, input, config=None):
        try:
            await asyncio.sleep(3600.0)
        except asyncio.CancelledError:
            self.cancelled_seen = True
            raise

    async def abatch(self, inputs, config=None):
        return [{"echo": i} for i in inputs]


def test_br108_cancelled_invoke_emits_499_wide_event_best_effort(
    http_request_log_capture: list[Any],
) -> None:
    """Given slow ainvoke, when wait times out, then stub cancels and log shows 499 best-effort."""

    # Given
    stub = _SlowRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )

    async def _run() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    client.post("/agents/agent1/invoke", json={"input": {}}),
                    timeout=0.25,
                )

    # When
    asyncio.run(_run())
    # Then
    assert stub.cancelled_seen is True
    all_ev = _http_request_events(http_request_log_capture)
    events = [e for e in all_ev if "invoke" in str(e.get("http.route", ""))]
    assert len(events) >= 1
    cancel_ev = events[-1]
    assert cancel_ev.get("http.status_code") == 499
    # Clean


def test_probe_http_route_uses_create_app_prefix(
    http_request_log_capture: list[Any],
) -> None:
    """Given create_app_prefix /api, when GET /api/health, then http.route is /api/health."""

    # Given
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"x": StubRunnable()}),
        create_app_prefix="/api",
    )
    # When
    with TestClient(app) as client:
        client.get("/api/health")
    # Then
    ev = _http_request_events(http_request_log_capture)[0]
    assert ev["http.route"] == "/api/health"
    # Clean
