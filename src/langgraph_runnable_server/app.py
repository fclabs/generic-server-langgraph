"""FastAPI probe app (health, metrics); see ``specs/01-fastapi-server.md``."""

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
    """Map non-GET on probe paths to 404 instead of 405 (FR-014, BR-006)."""

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
    """Probe-only FastAPI app: ``{base}/health`` and ``{base}/metrics`` (FR-001–FR-014, spec 01)."""
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
