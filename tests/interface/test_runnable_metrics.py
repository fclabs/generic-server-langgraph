"""Prometheus metrics for create_runnable_app (spec 02 iteration 4).

Covers VC-101, VC-104–VC-108, VC-115–VC-117, VC-121, NFR-106, BR-203.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import AsyncIterator
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.runnables import Runnable
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY
from prometheus_client.parser import text_string_to_metric_families
from runnable_stubs import StubRunnable

from langgraph_runnable_server import create_runnable_app

BR_202_LES = {
    "0.005",
    "0.01",
    "0.025",
    "0.05",
    "0.1",
    "0.25",
    "0.5",
    "1.0",
    "2.5",
    "5.0",
    "10.0",
    "+Inf",
}


def _samples(text: str) -> list:
    out = []
    for fam in text_string_to_metric_families(text):
        out.extend(fam.samples)
    return out


def _counter_value(text: str, name: str, labels: dict[str, str]) -> float:
    for s in _samples(text):
        if s.name == name and dict(s.labels) == labels:
            return float(s.value)
    raise AssertionError(f"missing counter {name} {labels}")


def _histogram_sum(text: str, name_prefix: str, labels: dict[str, str]) -> float:
    want = f"{name_prefix}_sum"
    for s in _samples(text):
        if s.name == want and {k: v for k, v in s.labels.items() if k != "le"} == labels:
            return float(s.value)
    raise AssertionError(f"missing histogram sum {want} {labels}")


def _histogram_count(text: str, name_prefix: str, labels: dict[str, str]) -> float:
    want = f"{name_prefix}_count"
    for s in _samples(text):
        if s.name == want and dict(s.labels) == labels:
            return float(s.value)
    raise AssertionError(f"missing histogram count {want} {labels}")


def _duration_bucket_le_set(text: str, duration_metric_prefix: str) -> set[str]:
    bucket = f"{duration_metric_prefix}_bucket"
    les: set[str] = set()
    for s in _samples(text):
        if s.name == bucket and "le" in s.labels:
            les.add(s.labels["le"])
    return les


def _assert_families_parseable(text: str, name_prefix: str) -> None:
    """NFR-106 fallback: exposition parses and required metric families are declared."""
    for base in (
        "requests_total",
        "errors_total",
        "request_duration_seconds",
        "request_size_bytes",
        "response_size_bytes",
    ):
        assert f"# TYPE {name_prefix}{base}" in text
    list(text_string_to_metric_families(text))


def test_vc101_full_probes_runnable_and_metrics_content_type() -> None:
    """Given runnable POST then GET /metrics, then Prometheus text and exact Content-Type."""
    # Given
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        inv = client.post("/agents/agent1/invoke", json={"input": 1})
        metrics = client.get("/metrics")
    # Then
    assert inv.status_code == 200
    assert metrics.status_code == 200
    assert metrics.headers.get("content-type") == CONTENT_TYPE_LATEST
    assert b"langgraph_runnable_server_requests_total" in metrics.content


def test_vc107_exact_request_counters_per_labels() -> None:
    """Mixed invoke/batch on two keys: three counters at 1; no agent2 batch series."""
    # Given
    s1 = StubRunnable()
    s2 = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": s1, "agent2": s2}),
    )
    prefix = "langgraph_runnable_server"
    # When
    with TestClient(app) as client:
        client.post("/agents/agent1/invoke", json={"input": 1})
        client.post("/agents/agent1/batch", json={"inputs": [1]})
        client.post("/agents/agent2/invoke", json={"input": 2})
        text = client.get("/metrics").text
    # Then
    rt = f"{prefix}_requests_total"
    a1i = {"runnable": "agent1", "endpoint": "invoke"}
    a1b = {"runnable": "agent1", "endpoint": "batch"}
    a2i = {"runnable": "agent2", "endpoint": "invoke"}
    assert _counter_value(text, rt, a1i) == 1.0
    assert _counter_value(text, rt, a1b) == 1.0
    assert _counter_value(text, rt, a2i) == 1.0
    for s in _samples(text):
        if s.name == f"{prefix}_requests_total" and s.labels.get("endpoint") == "batch":
            assert s.labels.get("runnable") != "agent2"


def test_vc105_full_500_increments_errors_and_duration_count() -> None:
    """Runnable raises: 5xx errors_total, requests_total, duration histogram count."""
    # Given
    stub = StubRunnable(raise_on_invoke=RuntimeError("boom"))
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    p = "langgraph_runnable_server"
    labels_req = {"runnable": "agent1", "endpoint": "invoke"}
    labels_err = {**labels_req, "http_status_class": "5xx"}
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/invoke", json={"input": {}})
        text = client.get("/metrics").text
    # Then
    assert r.status_code == 500
    assert _counter_value(text, f"{p}_requests_total", labels_req) == 1.0
    assert _counter_value(text, f"{p}_errors_total", labels_err) == 1.0
    assert _histogram_count(text, f"{p}_request_duration_seconds", labels_req) == 1.0


def test_vc104_full_422_increments_4xx_errors_and_requests() -> None:
    """Given invalid invoke body, when POST, then 422, errors_total 4xx, requests_total."""
    # Given
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    p = "langgraph_runnable_server"
    labels_req = {"runnable": "agent1", "endpoint": "invoke"}
    labels_err = {**labels_req, "http_status_class": "4xx"}
    # When
    with TestClient(app) as client:
        r = client.post("/agents/agent1/invoke", json={})
        text = client.get("/metrics").text
    # Then
    assert r.status_code == 422
    assert _counter_value(text, f"{p}_requests_total", labels_req) == 1.0
    assert _counter_value(text, f"{p}_errors_total", labels_err) == 1.0


def test_vc108_request_and_response_size_histogram_sums() -> None:
    """Given fixed JSON bodies, when POST invoke, then size histogram sums match byte lengths."""
    # Given
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    p = "langgraph_runnable_server"
    labels = {"runnable": "agent1", "endpoint": "invoke"}
    payload = {"input": {"x": 1}}
    body_bytes = json.dumps(payload).encode()
    # When
    with TestClient(app) as client:
        r = client.post(
            "/agents/agent1/invoke",
            content=body_bytes,
            headers={"Content-Type": "application/json"},
        )
        text = client.get("/metrics").text
    # Then
    assert r.status_code == 200
    req_bytes = len(body_bytes)
    resp_bytes = len(r.content)
    assert _histogram_count(text, f"{p}_request_size_bytes", labels) == 1.0
    assert _histogram_count(text, f"{p}_response_size_bytes", labels) == 1.0
    assert _histogram_sum(text, f"{p}_request_size_bytes", labels) == float(req_bytes)
    assert _histogram_sum(text, f"{p}_response_size_bytes", labels) == float(resp_bytes)


def test_vc115_metrics_scrape_does_not_increment_requests() -> None:
    """Given one runnable POST, when GET /metrics five times, then requests_total stays 1."""
    # Given
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    p = "langgraph_runnable_server"
    labels = {"runnable": "agent1", "endpoint": "invoke"}
    # When
    with TestClient(app) as client:
        client.post("/agents/agent1/invoke", json={"input": 1})
        for _ in range(5):
            client.get("/metrics")
        text = client.get("/metrics").text
    # Then
    assert _counter_value(text, f"{p}_requests_total", labels) == 1.0


def test_vc116_duration_histogram_bucket_labels_match_br202() -> None:
    """Given a smoke POST, when scraping, then request_duration_seconds_bucket le= set is BR-202."""
    # Given
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        client.post("/agents/agent1/invoke", json={"input": 1})
        text = client.get("/metrics").text
    # Then
    les = _duration_bucket_le_set(text, "langgraph_runnable_server_request_duration_seconds")
    assert les == BR_202_LES


def test_vc117_two_apps_registry_isolation() -> None:
    """Two apps same routes: isolated scrapes; registries differ; not default REGISTRY."""
    # Given
    a = StubRunnable()
    b = StubRunnable()
    app1 = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": a}),
    )
    app2 = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": b}),
    )
    assert app1.state["metrics_registry"] is not app2.state["metrics_registry"]
    # When
    with TestClient(app1) as c1, TestClient(app2) as c2:
        c1.post("/agents/agent1/invoke", json={"input": "one"})
        c2.post("/agents/agent1/invoke", json={"input": "two"})
        t1 = c1.get("/metrics").text
        t2 = c2.get("/metrics").text
    # Then
    name = "langgraph_runnable_server_requests_total"
    labels = {"runnable": "agent1", "endpoint": "invoke"}
    assert _counter_value(t1, name, labels) == 1.0
    assert _counter_value(t2, name, labels) == 1.0
    reg_names = getattr(REGISTRY, "_names_to_collectors", {})
    assert "langgraph_runnable_server_requests_total" not in reg_names


def test_vc121_full_metric_name_expansion_default_custom_and_empty() -> None:
    """Default, custom, and empty metrics_namespace: exposition names match FR-123."""
    # Given
    stub = StubRunnable()
    app_default = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"x": stub}),
    )
    app_custom = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"x": stub}),
        metrics_namespace="acme_agents",
    )
    app_bare = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"x": stub}),
        metrics_namespace="",
    )
    # When
    with TestClient(app_default) as d, TestClient(app_custom) as c, TestClient(app_bare) as b:
        d.post("/agents/x/invoke", json={"input": 1})
        c.post("/agents/x/invoke", json={"input": 1})
        b.post("/agents/x/invoke", json={"input": 1})
        td = d.get("/metrics").text
        tc = c.get("/metrics").text
        tb = b.get("/metrics").text
    # Then
    assert "langgraph_runnable_server_requests_total" in td
    assert "acme_agents_requests_total" in tc
    for s in _samples(tc):
        assert not str(s.name).startswith("langgraph_runnable_server_")
    assert "# TYPE requests_total" in tb
    assert not any(str(s.name).startswith("langgraph_runnable_server_") for s in _samples(tb))


def test_nfr106_promtool_check_metrics_when_available() -> None:
    """Given a smoke POST, when promtool is on PATH, then `promtool check metrics` succeeds."""
    # Given
    promtool = shutil.which("promtool")
    if promtool is None:
        pytest.skip("promtool not on PATH")
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        client.post("/agents/agent1/invoke", json={"input": 1})
        raw = client.get("/metrics").content
    text = raw.decode()
    with tempfile.NamedTemporaryFile(suffix=".prom", delete=False) as tmp:
        tmp.write(raw)
        path = tmp.name
    try:
        subprocess.run([promtool, "check", "metrics", path], check=True, capture_output=True)
    finally:
        os.unlink(path)
    # Then
    _assert_families_parseable(text, "langgraph_runnable_server_")


def test_nfr106_fallback_parser_families_when_no_promtool() -> None:
    """When promtool is absent, assert required metric families still parse from exposition."""
    # Given
    if shutil.which("promtool"):
        pytest.skip("promtool present; dedicated promtool test covers this branch")
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    # When
    with TestClient(app) as client:
        client.post("/agents/agent1/invoke", json={"input": 1})
        text = client.get("/metrics").text
    # Then
    _assert_families_parseable(text, "langgraph_runnable_server_")


def test_br203_chunked_request_omits_request_size_histogram_count() -> None:
    """Chunked POST 422: request_size_bytes histogram count does not increase."""
    # Given
    stub = StubRunnable()
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"agent1": stub}),
    )
    p = "langgraph_runnable_server"
    labels = {"runnable": "agent1", "endpoint": "invoke"}

    async def bad_chunks() -> AsyncIterator[bytes]:
        yield b'{"foo": 1}'

    async def run_chunked() -> str:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/agents/agent1/invoke",
                headers={"Content-Type": "application/json"},
                content=bad_chunks(),
            )
            assert r.status_code == 422
            r2 = await ac.get("/metrics")
            return r2.text

    # When
    import asyncio

    with TestClient(app) as client:
        client.post("/agents/agent1/invoke", json={"input": 1})
        text_before = client.get("/metrics").text
    count_before = _histogram_count(text_before, f"{p}_request_size_bytes", labels)
    text_after = asyncio.run(run_chunked())
    count_after = _histogram_count(text_after, f"{p}_request_size_bytes", labels)
    # Then
    assert count_after == count_before
