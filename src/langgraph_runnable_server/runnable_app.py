"""FastAPI factory: probe app from ``create_app`` plus per-key runnable HTTP routes.

Public API::

    def create_runnable_app(
        *,
        prefix: str,
        runnables: dict[str, Runnable],
        create_app_prefix: str = "/",
        lifespan: Lifespan[FastAPI] | None = None,
        metrics_namespace: str = "langgraph_runnable_server",
    ) -> FastAPI: ...

Validation order:

1. ``runnables`` must be a plain ``dict`` (else ``TypeError``; ``UserDict`` /
   ``MappingProxyType`` rejected).
2. ``metrics_namespace`` must be ``str`` (else ``TypeError``); empty allowed; non-empty must match
   ``^[a-zA-Z_][a-zA-Z0-9_]*$`` (else ``ValueError``).
3. Every key must match ``^[A-Za-z0-9._-]{1,64}$`` (``ValueError``).
4. Runnable ``prefix``: same normalization as ``create_app`` / FR-011 (``_normalize_prefix``).
5. Path collision (FR-108): each ``{runnables_base}/{key}/invoke|batch`` vs
   ``{probe_base}/health`` and ``{probe_base}/metrics`` using **strict overlap** (not equality
   only): equal paths; runnable under a probe prefix; or probe under a runnable prefix
   (shadowing). Covers VC-114 (e.g. ``prefix="/health"``, key ``x``) despite FR-108 "full path"
   wording.

After validation, **POST** routes are registered with **literal paths** per runnable key (no
``{key}`` path parameter), so unknown keys yield FastAPI's default **404**. For each key ``k`` in
``runnables``:

* ``POST {runnables_base}/k/invoke`` — JSON object with ``input`` (required; any JSON value,
  including ``null``) and optional ``config``; response **200** and JSON from ``jsonable_encoder``
  (BR-103).
* ``POST {runnables_base}/k/batch`` — object with ``inputs`` (required; must be a JSON array) and
  optional ``config``; response **200** and ``jsonable_encoder`` of the batch result. If
  ``inputs`` is ``[]``, the handler returns ``[]`` **without** calling ``abatch`` (BR-102).

When ``runnables_base`` is empty (normalized runnable prefix is root), paths are
``POST /{k}/invoke`` and ``POST /{k}/batch``.

**Error responses**

* **422** — Client / body validation: JSON ``{"detail": "<message>"}``. Triggers include: non-empty
  body without ``Content-Type: application/json`` (media type, case-insensitive); JSON parse
  failure; root JSON value not an object; ``invoke`` without an ``input`` key; ``batch`` without
  ``inputs`` or ``inputs`` not a list.
* **500** — Uncaught ``Exception`` from ``ainvoke`` / ``abatch``: ``{"detail": "<message>"}`` (no
  traceback in the response). The exception object is stored on ``request.state.exception`` for
  later logging (iter 5). ``BaseException`` subclasses outside ``Exception`` (e.g.
  ``asyncio.CancelledError``) are not handled here and propagate per BR-108.
* **405** — Non-``POST`` on a registered runnable path (Starlette default).
* **404** — Unknown runnable key (no matching route).

**Cancellation (BR-108):** cooperative cancellation is not swallowed; on ``asyncio.CancelledError``
the handler sets ``request.state.cancelled`` to a true value and re-raises.

**Route template note (VC-109 / BR-301):** logging in iter 5 will treat the registered path string
as ``http.route`` (e.g. ``/agents/agent1/invoke``), not a parameterized ``/agents/{key}/invoke``.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from langchain_core.runnables import Runnable
from starlette.types import Lifespan

from ._prefix import _normalize_prefix
from .app import create_app

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


def _make_invoke_handler(runnable: Runnable):
    async def handler(request: Request) -> JSONResponse:
        body = await _load_json_object(request)
        if "input" not in body:
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
            return JSONResponse({"detail": str(exc)}, status_code=500)
        return JSONResponse(content=jsonable_encoder(result), status_code=200)

    return handler


def _make_batch_handler(runnable: Runnable):
    async def handler(request: Request) -> JSONResponse:
        body = await _load_json_object(request)
        if "inputs" not in body:
            raise HTTPException(
                status_code=422,
                detail="Request body must include an 'inputs' key",
            )
        inputs = body["inputs"]
        if not isinstance(inputs, list):
            raise HTTPException(
                status_code=422,
                detail="'inputs' must be a JSON array",
            )
        if inputs == []:
            return JSONResponse(content=[], status_code=200)
        config = body.get("config")
        try:
            result = await runnable.abatch(inputs, config=config)
        except asyncio.CancelledError:
            request.state.cancelled = True
            raise
        except Exception as exc:
            request.state.exception = exc
            return JSONResponse({"detail": str(exc)}, status_code=500)
        return JSONResponse(content=jsonable_encoder(result), status_code=200)

    return handler


def create_runnable_app(
    *,
    prefix: str,
    runnables: dict[str, Runnable],
    create_app_prefix: str = "/",
    lifespan: Lifespan[FastAPI] | None = None,
    metrics_namespace: str = "langgraph_runnable_server",
) -> FastAPI:
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

    app = create_app(prefix=create_app_prefix, lifespan=lifespan)
    app.state["metrics_namespace"] = metrics_namespace

    for key, runnable in runnables.items():
        invoke_p = _invoke_path(runnables_base, key)
        batch_p = _batch_path(runnables_base, key)
        app.add_api_route(invoke_p, _make_invoke_handler(runnable), methods=["POST"])
        app.add_api_route(batch_p, _make_batch_handler(runnable), methods=["POST"])

    return app
