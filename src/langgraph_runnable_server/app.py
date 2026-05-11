"""Application factory: FastAPI app with health, metrics, instance_id, and default lifespan."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import Lifespan

from .api.routes.health import router as health_router
from .api.routes.metrics import router as metrics_router


@asynccontextmanager
async def _default_lifespan(app: FastAPI):
    yield


def create_app(
    prefix: str = "/",
    lifespan: Lifespan[FastAPI] | None = None,
) -> FastAPI:
    """Return a FastAPI app with default-prefix routes and library defaults.

    The app exposes ``GET /health`` (plain body ``ok``) and ``GET /metrics`` (empty body),
    sets ``app.state[\"instance_id\"]`` to a new UUID string per invocation (FR-001), and
    registers a no-op async lifespan (FR-003, FR-004, FR-005). The ``lifespan`` argument is
    accepted for API stability but is not yet forwarded; that arrives in iteration 4.

    Prefix behavior is extended in iteration 3 (FR-011).
    """
    del prefix  # Wired in iteration 3 (FR-011).
    del lifespan  # Wired in iteration 4 (FR-003); iteration 2 always uses the no-op default.
    app = FastAPI(lifespan=_default_lifespan)
    app.state.instance_id = str(uuid.uuid4())
    app.include_router(health_router)
    app.include_router(metrics_router)
    return app
