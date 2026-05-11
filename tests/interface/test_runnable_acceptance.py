"""VC-120: single end-to-end acceptance test for ``create_runnable_app`` (spec 02)."""

from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient
from langchain_core.runnables import Runnable
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry
from prometheus_client.parser import text_string_to_metric_families
from runnable_stubs import StubRunnable
from structlog.testing import capture_logs

import langgraph_runnable_server as lgrs


def _http_request_events(entries: list[Any]) -> list[dict[str, Any]]:
    return [e for e in entries if e.get("event") == "http_request"]


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


def _histogram_count(text: str, name_prefix: str, labels: dict[str, str]) -> float:
    want = f"{name_prefix}_count"
    for s in _samples(text):
        if s.name == want and dict(s.labels) == labels:
            return float(s.value)
    raise AssertionError(f"missing histogram count {want} {labels}")


def test_full_runnable_surface() -> None:
    """End-to-end VC-120: probes, routes, errors, metrics, logs, and ``__all__``."""

    # Given
    r1 = StubRunnable()
    r2 = StubRunnable()
    boom = StubRunnable(raise_on_invoke=RuntimeError("boom"))
    runnables = cast(
        dict[str, Runnable],
        {"agent1": r1, "agent2": r2, "boom": boom},
    )
    with capture_logs() as cap:
        app = lgrs.create_runnable_app(
            prefix="/agents",
            runnables=runnables,
            create_app_prefix="/",
        )
        # When / Then — 1. Probes intact (spec 01)
        with TestClient(app) as client:
            health = client.get("/health")
            assert health.status_code == 200
            assert health.content == b"ok"

            # 2. Routing isolation (FR-102, FR-104)
            inv = client.post("/agents/agent1/invoke", json={"input": {"x": 1}})
            assert inv.status_code == 200
            assert inv.json() == {"echo": {"x": 1}}
            assert len(r1.ainvoke_calls) == 1
            assert r2.ainvoke_calls == []

            # 3. Batch (FR-103, BR-102)
            bat = client.post(
                "/agents/agent2/batch",
                json={"inputs": [{"x": 1}, {"x": 2}]},
            )
            assert bat.status_code == 200
            assert bat.json() == [{"echo": {"x": 1}}, {"echo": {"x": 2}}]
            assert r2.abatch_calls[0][0] == [{"x": 1}, {"x": 2}]

            # 4. Method discipline (BR-105)
            assert client.get("/agents/agent1/invoke").status_code == 405
            assert client.post("/agents/no_such_key/invoke", json={"input": 1}).status_code == 404

            # 5. Validation (BR-104) — spec names agent1 for this 422
            bad = client.post("/agents/agent1/invoke", json={})
            assert bad.status_code == 422
            assert "detail" in bad.json()

            # 6. Runnable exception (FR-109) on dedicated key ``boom``
            err = client.post("/agents/boom/invoke", json={"input": 1})
            assert err.status_code == 500
            body = err.json()
            assert "detail" in body
            raw = err.text
            assert "Traceback" not in raw
            assert 'File "' not in raw

            # 7–8. Metrics (default namespace) + registry (FR-122, FR-123)
            metrics = client.get("/metrics")
            assert metrics.status_code == 200
            assert metrics.headers.get("content-type") == CONTENT_TYPE_LATEST
            text = metrics.text
            ns = "langgraph_runnable_server"
            rt = f"{ns}_requests_total"
            et = f"{ns}_errors_total"
            dprefix = f"{ns}_request_duration_seconds"
            # Every routed invoke/batch (including 4xx/5xx) increments ``requests_total``.
            assert _counter_value(text, rt, {"runnable": "agent1", "endpoint": "invoke"}) == 2.0
            assert _counter_value(text, rt, {"runnable": "agent2", "endpoint": "batch"}) == 1.0
            assert _counter_value(text, rt, {"runnable": "boom", "endpoint": "invoke"}) == 1.0
            assert (
                _counter_value(
                    text,
                    et,
                    {
                        "runnable": "agent1",
                        "endpoint": "invoke",
                        "http_status_class": "4xx",
                    },
                )
                == 1.0
            )
            assert (
                _counter_value(
                    text,
                    et,
                    {"runnable": "boom", "endpoint": "invoke", "http_status_class": "5xx"},
                )
                == 1.0
            )
            lbl_a1 = {"runnable": "agent1", "endpoint": "invoke"}
            lbl_a2b = {"runnable": "agent2", "endpoint": "batch"}
            lbl_bm = {"runnable": "boom", "endpoint": "invoke"}
            assert _histogram_count(text, dprefix, lbl_a1) == 2.0
            assert _histogram_count(text, dprefix, lbl_a2b) == 1.0
            assert _histogram_count(text, dprefix, lbl_bm) == 1.0

        reg = app.state["metrics_registry"]
        assert isinstance(reg, CollectorRegistry)
        assert reg is not REGISTRY
        assert app.state["metrics_namespace"] == "langgraph_runnable_server"

        # 9. Structlog wide events (FR-130, BR-301)
        events = _http_request_events(cap)
        assert len(events) == 8
        for e in events:
            assert isinstance(e["duration_ms"], float)
            assert "http.method" in e
            assert "http.route" in e
            assert "http.status_code" in e
            assert "instance_id" in e
            assert "response_size_bytes" in e
            assert isinstance(e["response_size_bytes"], int)

        runnable_events = [e for e in events if "runnable" in e]
        assert len(runnable_events) == 4
        for e in runnable_events:
            assert e["endpoint"] in ("invoke", "batch")
            assert e["runnable"] in ("agent1", "agent2", "boom")

        probe_like = [e for e in events if "runnable" not in e]
        assert len(probe_like) == 4

        boom_ev = next(e for e in runnable_events if e.get("runnable") == "boom")
        assert boom_ev["error.type"] == "RuntimeError"
        assert "Traceback" in boom_ev["error.stack"]

        # 10. __all__ (FR-112)
        assert set(lgrs.__all__) == {"create_app", "create_runnable_app"}
