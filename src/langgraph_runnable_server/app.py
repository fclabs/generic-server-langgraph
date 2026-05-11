"""Application factory: FastAPI app with health, metrics, instance_id, and default lifespan."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import Lifespan

from .api.routes.health import router as health_router
from .api.routes.metrics import router as metrics_router

_ASCII_WHITESPACE = " \t\n\r\x0b\x0c"

_UNRESERVED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
_SUB_DELIMS = frozenset("!$&'()*+,;=")
_PCHAR_SINGLE = _UNRESERVED | _SUB_DELIMS | frozenset(":@")
_HEX = frozenset("0123456789abcdefABCDEF")


@asynccontextmanager
async def _default_lifespan(app: FastAPI):
    yield


def _normalize_prefix(prefix: str) -> str:
    """Normalize ``prefix`` to the mount path for routers (FR-011).

    Order: trim ASCII whitespace; reject if ``//`` appears anywhere; empty after trim is
    treated as ``/`` for the remaining steps; require a leading ``/``; strip trailing
    slashes until root ``/`` or no trailing slash; root ``/`` yields ``\"\"`` (no base
    segment). Non-root paths are validated for RFC 3986 ``pchar`` (including ``pct-encoded``).
    """
    s = prefix.strip(_ASCII_WHITESPACE)
    if "//" in s:
        raise ValueError("prefix must not contain '//'")
    if not s:
        s = "/"
    if not s.startswith("/"):
        raise ValueError("prefix must start with '/'")
    while len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    if s == "/":
        return ""
    _validate_prefix_pchars(s)
    return s


def _validate_prefix_pchars(path: str) -> None:
    i = 0
    n = len(path)
    while i < n:
        c = path[i]
        if c == "/":
            i += 1
        elif c == "%":
            if i + 2 >= n or path[i + 1] not in _HEX or path[i + 2] not in _HEX:
                raise ValueError("prefix contains invalid character: '%'")
            i += 3
        elif c in _PCHAR_SINGLE:
            i += 1
        else:
            raise ValueError(f"prefix contains invalid character: {c!r}")


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
    else:
        app.include_router(health_router)
        app.include_router(metrics_router)
    return app
