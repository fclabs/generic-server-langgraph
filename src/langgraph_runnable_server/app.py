"""Application factory: FastAPI app with health, metrics, instance_id, and default lifespan."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Lifespan

from ._prefix import _normalize_prefix
from .api.routes.health import router as health_router
from .api.routes.metrics import router as metrics_router


@asynccontextmanager
async def _default_lifespan(app: FastAPI):
    yield


class _ProbeNonGetReturns404Middleware(BaseHTTPMiddleware):
    """Reject non-GET requests on probe URLs with 404 (FR-014, BR-006).

    Starlette would otherwise answer **405 Method Not Allowed** for routes registered only
    for ``GET``. This middleware is registered **last** so it runs **first** on the request
    and short-circuits before routing.
    """

    def __init__(self, app, probe_paths: frozenset[str]) -> None:
        super().__init__(app)
        self._probe_paths = probe_paths

    async def dispatch(self, request: Request, call_next):
        if request.method != "GET" and request.url.path in self._probe_paths:
            return Response(status_code=404)
        return await call_next(request)


def create_app(
    prefix: str = "/",
    lifespan: Lifespan[FastAPI] | None = None,
) -> FastAPI:
    """Return a FastAPI app with health and metrics under ``{base}/health`` and ``{base}/metrics``.

    ``{base}`` comes from ``prefix`` per **FR-011** in ``specs/01-fastapi-server.md``: ASCII
    trim; reject any ``//``; ``\"\"`` or whitespace-only after trim is treated as root; a
    non-empty value must start with ``/``; trailing slashes are stripped; root maps to
    ``/health`` and ``/metrics`` with no doubled slash; non-root paths must be ``/`` plus
    RFC 3986 ``pchar`` segments (including ``%HH``). Invalid prefixes raise ``ValueError``
    before any ``FastAPI`` instance is built.

    Sets ``app.state[\"instance_id\"]`` (FR-001). The ``lifespan`` parameter is
    **keyword-recommended** (FR-010). When ``lifespan`` is ``None`` (default), the library
    registers a no-op async lifespan (FR-003, FR-004, FR-005). When non-``None``, the value
    is passed to ``FastAPI(lifespan=...)`` unchanged—no wrapping or composition (FR-003,
    FR-013); it must satisfy Starlette's ``Lifespan[FastAPI]`` contract.

    Non-``GET`` requests to ``{base}/health`` or ``{base}/metrics`` return **404** (not 405);
    see **FR-014** / **BR-006** — enforced by :class:`_ProbeNonGetReturns404Middleware`.
    """
    base = _normalize_prefix(prefix)
    if lifespan is None:
        app = FastAPI(lifespan=_default_lifespan)
    else:
        app = FastAPI(lifespan=lifespan)
    app.state.instance_id = str(uuid.uuid4())
    if base:
        app.include_router(health_router, prefix=base)
        app.include_router(metrics_router, prefix=base)
        probe_paths = frozenset({f"{base}/health", f"{base}/metrics"})
    else:
        app.include_router(health_router)
        app.include_router(metrics_router)
        probe_paths = frozenset({"/health", "/metrics"})
    # Last registered = outermost = runs first (before routing emits 405 on probe paths).
    app.add_middleware(_ProbeNonGetReturns404Middleware, probe_paths=probe_paths)
    return app
