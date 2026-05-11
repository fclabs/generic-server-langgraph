"""Structlog helpers and wide-event field rules (FR-130, BR-301, NFR-108; see README NFR-110)."""

from __future__ import annotations

import re
import traceback

import structlog
from starlette.requests import Request

log = structlog.get_logger("langgraph_runnable_server")

TRACEPARENT_RE = re.compile(
    r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$",
)


def parse_trace_id(traceparent: str | None) -> str | None:
    """Parse W3C ``traceparent`` into a 32-char hex trace id, or ``None`` to omit (BR-301)."""
    if traceparent is None:
        return None
    raw = traceparent.strip()
    if not TRACEPARENT_RE.fullmatch(raw):
        return None
    return raw.split("-", 3)[1]


def transfer_encoding_chunked(request: Request) -> bool:
    """Return True when ``Transfer-Encoding`` includes ``chunked`` (BR-203)."""
    hdr = request.headers.get("transfer-encoding")
    if hdr is None:
        return False
    return "chunked" in hdr.lower()


def request_body_size_bytes_br203(
    request: Request, body_bytes: bytes, *, http_status: int
) -> int | None:
    """Request body size for metrics when allowed by BR-203, else ``None``."""
    if transfer_encoding_chunked(request):
        return None
    if http_status == 422:
        cl = request.headers.get("content-length")
        if cl is None:
            return None
        try:
            n = int(cl.strip())
        except ValueError:
            return None
        if n < 0:
            return None
        return n
    return len(body_bytes)


def wide_event_request_size_bytes(
    request: Request,
    *,
    http_status: int,
) -> int | None:
    """``request_size_bytes`` for the wide event (BR-203 + probe paths)."""
    rsz = getattr(request.state, "metrics_request_size", None)
    if rsz is not None:
        return rsz
    if transfer_encoding_chunked(request):
        return None
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return 0
    if http_status == 422:
        body_bytes: bytes = getattr(request.state, "_last_request_body_bytes", b"")
        return request_body_size_bytes_br203(request, body_bytes, http_status=422)
    return None


def http_route_for_request(request: Request) -> str:
    """Return matched route path, or ``request.url.path`` when unrouted (BR-301)."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return request.url.path


def format_error_stack(exc: BaseException) -> str:
    """Format ``exc`` as a traceback string for ``error.stack`` (VC-118)."""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
