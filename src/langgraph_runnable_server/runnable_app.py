"""Runnable HTTP factory, middleware, and handlers (see ``specs/02-runnable-support.md``)."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from langchain_core.runnables import Runnable
from prometheus_client import CollectorRegistry
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import Lifespan
from structlog.exceptions import DropEvent

from ._prefix import _normalize_prefix
from .app import create_app
from .logging import (
    format_error_stack,
    http_route_for_request,
    log,
    parse_trace_id,
    request_body_size_bytes_br203,
    wide_event_request_size_bytes,
)
from .metrics.families import MetricFamilies, build_metrics

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_METRICS_NAMESPACE_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _content_type_media_type(content_type: str | None) -> str | None:
    if content_type is None:
        return None
    stripped = content_type.strip()
    if not stripped:
        return None
    return stripped.split(";", 1)[0].strip().lower() or None


async def _load_json_object(request: Request) -> dict[str, Any]:
    body_bytes = await request.body()
    request.state._last_request_body_bytes = body_bytes
    if len(body_bytes) > 0:
        media = _content_type_media_type(request.headers.get("content-type"))
        if media != "application/json":
            raise HTTPException(
                status_code=422,
                detail="Content-Type must be application/json",
            )
    try:
        parsed: object = json.loads(body_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="JSON body must be an object")
    return cast(dict[str, Any], parsed)


class _RunnableRequestMiddleware(BaseHTTPMiddleware):
    """Runnable Prometheus (BR-106) + one structlog ``http_request`` event per request (FR-130)."""

    def __init__(self, app, *, families: MetricFamilies) -> None:
        super().__init__(app)
        self._families = families

    async def dispatch(self, request: Request, call_next):
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except asyncio.CancelledError:
            t1 = time.perf_counter()
            self._complete(request, None, t0, t1, cancelled=True)
            raise
        t1 = time.perf_counter()
        self._complete(request, response, t0, t1, cancelled=False)
        return response

    def _complete(
        self,
        request: Request,
        response: Response | None,
        t0: float,
        t1: float,
        *,
        cancelled: bool,
    ) -> None:
        if getattr(request.state, "_http_request_wide_event_done", False):
            return
        request.state._http_request_wide_event_done = True

        elapsed_s = t1 - t0
        duration_ms = elapsed_s * 1000.0

        if response is not None:
            runnable = getattr(request.state, "runnable", None)
            endpoint = getattr(request.state, "endpoint", None)
            if runnable is not None and endpoint is not None:
                labels = {"runnable": runnable, "endpoint": endpoint}
                self._families.requests_total.labels(**labels).inc()
                status = response.status_code
                if 400 <= status < 500:
                    self._families.errors_total.labels(
                        runnable=runnable, endpoint=endpoint, http_status_class="4xx"
                    ).inc()
                elif status >= 500:
                    self._families.errors_total.labels(
                        runnable=runnable, endpoint=endpoint, http_status_class="5xx"
                    ).inc()
                self._families.request_duration_seconds.labels(**labels).observe(elapsed_s)
                rsz = getattr(request.state, "metrics_request_size", None)
                if rsz is not None:
                    self._families.request_size_bytes.labels(**labels).observe(rsz)
                resp_len = getattr(request.state, "metrics_response_size", None)
                if resp_len is None:
                    raw = getattr(response, "body", None) or b""
                    resp_len = len(raw)
                self._families.response_size_bytes.labels(**labels).observe(resp_len)

        if cancelled or getattr(request.state, "cancelled", False):
            status_code = 499
        elif response is not None:
            status_code = response.status_code
        else:
            status_code = 500

        route = http_route_for_request(request)
        inst = getattr(request.app.state, "instance_id", None)
        instance_id = str(inst) if inst is not None else ""

        rsz_log = wide_event_request_size_bytes(request, http_status=status_code)
        if response is not None:
            raw_body = getattr(response, "body", None) or b""
            resp_size = len(raw_body)
        else:
            resp_size = 0

        fields: dict[str, Any] = {
            "http.method": request.method,
            "http.route": route,
            "http.status_code": status_code,
            "duration_ms": duration_ms,
            "instance_id": instance_id,
            "response_size_bytes": resp_size,
        }
        tid = parse_trace_id(request.headers.get("traceparent"))
        if tid is not None:
            fields["trace_id"] = tid
        runnable = getattr(request.state, "runnable", None)
        endpoint = getattr(request.state, "endpoint", None)
        if runnable is not None and endpoint is not None:
            fields["runnable"] = runnable
            fields["endpoint"] = endpoint
        if rsz_log is not None:
            fields["request_size_bytes"] = rsz_log

        exc = getattr(request.state, "exception", None)
        if exc is not None:
            fields["error.type"] = type(exc).__name__
            fields["error.stack"] = format_error_stack(exc)

        try:
            log.info("http_request", **fields)
        except DropEvent:
            pass
        except Exception:
            pass


def _probe_paths(probe_base: str) -> tuple[str, str]:
    if probe_base:
        return f"{probe_base}/health", f"{probe_base}/metrics"
    return "/health", "/metrics"


def _runnable_full_paths(runnables_base: str, key: str) -> tuple[str, str]:
    if runnables_base:
        stem = f"{runnables_base}/{key}"
    else:
        stem = f"/{key}"
    return f"{stem}/invoke", f"{stem}/batch"


def _paths_collide(a: str, b: str) -> bool:
    if a == b:
        return True
    if a.startswith(b + "/"):
        return True
    if b.startswith(a + "/"):
        return True
    return False


def _invoke_path(runnables_base: str, key: str) -> str:
    if runnables_base:
        return f"{runnables_base}/{key}/invoke"
    return f"/{key}/invoke"


def _batch_path(runnables_base: str, key: str) -> str:
    if runnables_base:
        return f"{runnables_base}/{key}/batch"
    return f"/{key}/batch"


def _make_invoke_handler(key: str, runnable: Runnable):
    async def handler(request: Request) -> JSONResponse:
        request.state.runnable = key
        request.state.endpoint = "invoke"
        try:
            body = await _load_json_object(request)
        except HTTPException:
            body_bytes = getattr(request.state, "_last_request_body_bytes", b"")
            request.state.metrics_request_size = request_body_size_bytes_br203(
                request, body_bytes, http_status=422
            )
            raise
        body_bytes: bytes = getattr(request.state, "_last_request_body_bytes", b"")
        if "input" not in body:
            request.state.metrics_request_size = request_body_size_bytes_br203(
                request, body_bytes, http_status=422
            )
            raise HTTPException(
                status_code=422,
                detail="Request body must include an 'input' key",
            )
        input_ = body["input"]
        config = body.get("config")
        try:
            result = await runnable.ainvoke(input_, config=config)
        except asyncio.CancelledError:
            request.state.cancelled = True
            raise
        except Exception as exc:
            request.state.exception = exc
            request.state.metrics_request_size = request_body_size_bytes_br203(
                request, body_bytes, http_status=500
            )
            resp = JSONResponse({"detail": str(exc)}, status_code=500)
            request.state.metrics_response_size = len(resp.body)
            return resp
        resp = JSONResponse(content=jsonable_encoder(result), status_code=200)
        request.state.metrics_request_size = request_body_size_bytes_br203(
            request, body_bytes, http_status=200
        )
        request.state.metrics_response_size = len(resp.body)
        return resp

    return handler


def _make_batch_handler(key: str, runnable: Runnable):
    async def handler(request: Request) -> JSONResponse:
        request.state.runnable = key
        request.state.endpoint = "batch"
        try:
            body = await _load_json_object(request)
        except HTTPException:
            body_bytes = getattr(request.state, "_last_request_body_bytes", b"")
            request.state.metrics_request_size = request_body_size_bytes_br203(
                request, body_bytes, http_status=422
            )
            raise
        body_bytes: bytes = getattr(request.state, "_last_request_body_bytes", b"")
        if "inputs" not in body:
            request.state.metrics_request_size = request_body_size_bytes_br203(
                request, body_bytes, http_status=422
            )
            raise HTTPException(
                status_code=422,
                detail="Request body must include an 'inputs' key",
            )
        inputs = body["inputs"]
        if not isinstance(inputs, list):
            request.state.metrics_request_size = request_body_size_bytes_br203(
                request, body_bytes, http_status=422
            )
            raise HTTPException(
                status_code=422,
                detail="'inputs' must be a JSON array",
            )
        if inputs == []:
            resp = JSONResponse(content=[], status_code=200)
            request.state.metrics_request_size = request_body_size_bytes_br203(
                request, body_bytes, http_status=200
            )
            request.state.metrics_response_size = len(resp.body)
            return resp
        config = body.get("config")
        try:
            result = await runnable.abatch(inputs, config=config)
        except asyncio.CancelledError:
            request.state.cancelled = True
            raise
        except Exception as exc:
            request.state.exception = exc
            request.state.metrics_request_size = request_body_size_bytes_br203(
                request, body_bytes, http_status=500
            )
            resp = JSONResponse({"detail": str(exc)}, status_code=500)
            request.state.metrics_response_size = len(resp.body)
            return resp
        resp = JSONResponse(content=jsonable_encoder(result), status_code=200)
        request.state.metrics_request_size = request_body_size_bytes_br203(
            request, body_bytes, http_status=200
        )
        request.state.metrics_response_size = len(resp.body)
        return resp

    return handler


def create_runnable_app(
    *,
    prefix: str,
    runnables: dict[str, Runnable],
    create_app_prefix: str = "/",
    lifespan: Lifespan[FastAPI] | None = None,
    metrics_namespace: str = "langgraph_runnable_server",
) -> FastAPI:
    """Compose probes plus per-key ``POST …/invoke`` and ``POST …/batch`` (FR-110, spec 02)."""
    if not isinstance(runnables, dict):
        raise TypeError("runnables must be a dict")

    if not isinstance(metrics_namespace, str):
        raise TypeError("metrics_namespace must be str")
    if metrics_namespace and not _METRICS_NAMESPACE_PATTERN.fullmatch(metrics_namespace):
        raise ValueError("metrics_namespace must match ^[a-zA-Z_][a-zA-Z0-9_]*$ when non-empty")

    for key in runnables:
        if not _KEY_PATTERN.fullmatch(key):
            raise ValueError(
                f"runnable key {key!r} must match ^[A-Za-z0-9._-]{{1,64}}$ (FR-107, BR-107)"
            )

    runnables_base = _normalize_prefix(prefix)
    probe_base = _normalize_prefix(create_app_prefix)
    health_path, metrics_path = _probe_paths(probe_base)

    for key in runnables:
        for path in _runnable_full_paths(runnables_base, key):
            for probe in (health_path, metrics_path):
                if _paths_collide(path, probe):
                    raise ValueError(
                        f"runnable path {path!r} collides with probe path {probe!r} (FR-108)"
                    )

    registry = CollectorRegistry()
    metric_families = build_metrics(metrics_namespace, registry)

    app = create_app(prefix=create_app_prefix, lifespan=lifespan)
    app.state["metrics_namespace"] = metrics_namespace
    app.state["metrics_registry"] = registry

    app.add_middleware(_RunnableRequestMiddleware, families=metric_families)

    for key, runnable in runnables.items():
        invoke_p = _invoke_path(runnables_base, key)
        batch_p = _batch_path(runnables_base, key)
        app.add_api_route(invoke_p, _make_invoke_handler(key, runnable), methods=["POST"])
        app.add_api_route(batch_p, _make_batch_handler(key, runnable), methods=["POST"])

    return app
