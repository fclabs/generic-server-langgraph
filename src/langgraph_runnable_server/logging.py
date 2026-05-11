"""Structured logging helpers for ``create_runnable_app`` (FR-130, BR-301, NFR-108).

The library uses ``structlog.get_logger("langgraph_runnable_server")`` only. It does **not**
call ``structlog.configure`` at import time; the host owns processor chains and output sinks.

**Wide event** (middleware, **INFO**, event name ``"http_request"``) fields — omit means the key
is absent from the event dict (not ``None``):

* ``http.method`` — ``str``, HTTP verb.
* ``http.route`` — ``str``: matched route path template when routing matched (literal per-key
  runnable paths, e.g. ``/agents/agent1/invoke``); for probes ``{probe_base}/health`` or
  ``{probe_base}/metrics``; if there is no matched route (e.g. **404**), the literal path
  ``request.url.path`` (BR-301 / VC-109 literal-path registration note).
* ``http.status_code`` — ``int``; cooperative cancellation (``request.state.cancelled``) is
  logged as **499** (best-effort) even though no HTTP response is returned to the client.
* ``duration_ms`` — ``float``, wall time for the request in milliseconds.
* ``instance_id`` — ``str`` from ``app.state.instance_id``.
* ``runnable`` — ``str``, runnable key; **omit** on probe and non-runnable traffic.
* ``endpoint`` — ``"invoke"`` or ``"batch"``; **omit** when ``runnable`` is omitted.
* ``request_size_bytes`` — non-negative ``int`` when the request body size is known per **BR-203**
  (same rule as the Prometheus ``request_size_bytes`` histogram); **omit** when chunked or
  otherwise unknowable.
* ``response_size_bytes`` — non-negative ``int``, **always** present (``0`` if there is no
  response body object, e.g. cancellation).
* ``trace_id`` — 32-char lowercase hex from W3C ``traceparent`` when the header matches the
  spec regex; **omit** otherwise.
* ``error.type`` — ``str``, exception class name when ``request.state.exception`` is set.
* ``error.stack`` — ``str``, formatted traceback for that exception (VC-118); only with
  ``error.type``.
"""

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
    """Return the 32-char trace id from ``traceparent`` or ``None`` to omit the log field."""
    if traceparent is None:
        return None
    raw = traceparent.strip()
    if not TRACEPARENT_RE.fullmatch(raw):
        return None
    # version-trace_id-span_id-flags
    return raw.split("-", 3)[1]


def transfer_encoding_chunked(request: Request) -> bool:
    """True when ``Transfer-Encoding`` lists ``chunked`` (BR-203 omit path)."""
    hdr = request.headers.get("transfer-encoding")
    if hdr is None:
        return False
    return "chunked" in hdr.lower()


def request_body_size_bytes_br203(
    request: Request, body_bytes: bytes, *, http_status: int
) -> int | None:
    """BR-203: size for metrics / wide event, or ``None`` to omit."""
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
    """Resolve ``request_size_bytes`` for the wide event (BR-203 + probes / unmatched)."""
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
    """Registered route path, or literal URL path when unrouted."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return request.url.path


def format_error_stack(exc: BaseException) -> str:
    """Formatted traceback string for ``error.stack``."""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
