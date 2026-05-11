# langgraph-runnable-server

Minimal FastAPI library exposing health and metrics endpoints under a configurable base path, plus a second factory that hosts LangChain/LangGraph `Runnable` HTTP endpoints. See [specs/01-fastapi-server.md](specs/01-fastapi-server.md), [specs/02-runnable-support.md](specs/02-runnable-support.md), and amendments in spec 02 (“Amendments to spec 01”) for `__all__` and `/metrics` behavior when using `create_runnable_app`.

## Acceptance

Spec 01 public surface (**VC-021**):

```bash
uv run pytest tests/interface/test_acceptance.py::test_full_public_surface -q
```

Spec 02 runnable surface (**VC-120**):

```bash
uv run pytest tests/interface/test_runnable_acceptance.py::test_full_runnable_surface -q
```

## Quick start

```python
from langgraph_runnable_server import create_app

app = create_app()
```

Use any ASGI host to serve `app` (for example Uvicorn). With the server listening on port 8000, an illustrative probe:

```bash
curl -sS http://127.0.0.1:8000/health
# ok
```

The host process owns bind address, port, and TLS; the library only supplies the ASGI `app`.

Only **GET** is defined on `/health` and `/metrics` (under `{base}`); any other HTTP method on those exact paths returns **404** (not 405). See **FR-014** / **BR-006** in spec 01.

## Runnable HTTP surface

`create_runnable_app` composes `create_app`, validates keys and path overlap against probe routes, attaches a dedicated Prometheus `CollectorRegistry`, registers **one literal POST path per runnable key** for `invoke` and `batch`, and adds middleware that records runnable metrics and emits **exactly one** structlog wide event per HTTP request (probes, scrapes, runnable routes, and unmatched paths).

```python
from langgraph_runnable_server import create_runnable_app


class Echo:
    async def ainvoke(self, input, config=None):
        return {"echo": input}

    async def abatch(self, inputs, config=None):
        return [{"echo": i} for i in inputs]


app = create_runnable_app(
    prefix="/agents",
    runnables={
        "agent1": Echo(),
        "agent2": Echo(),
    },
)
```

With Uvicorn on port 8000:

```bash
curl -sS -X POST http://127.0.0.1:8000/agents/agent1/invoke \
  -H 'Content-Type: application/json' \
  -d '{"input": {"q": "hello"}}'

curl -sS -X POST http://127.0.0.1:8000/agents/agent2/batch \
  -H 'Content-Type: application/json' \
  -d '{"inputs": [{"a": 1}, {"a": 2}]}'
```

Optional keyword arguments: `create_app_prefix` (probe base, forwarded verbatim to `create_app`), `lifespan` (same), and `metrics_namespace` (Prometheus name prefix; default `langgraph_runnable_server`; empty string means no prefix on metric names).

### Request bodies

- `POST {prefix}/{key}/invoke` — JSON **object** with required `input` (any JSON value, including `null`) and optional `config` (second argument to `Runnable.ainvoke`).
- `POST {prefix}/{key}/batch` — JSON **object** with required `inputs` (JSON array) and optional `config` (second argument to `Runnable.abatch`). If `inputs` is `[]`, the server returns `[]` **without** calling `abatch` (**BR-102**).

Non-empty bodies must send `Content-Type: application/json` (media type, case-insensitive). The JSON root must be an object (**BR-109**).

### Response bodies

Successful runnable calls return **200** with JSON from FastAPI’s `jsonable_encoder` (**BR-103**): plain JSON for Pydantic models, datetimes, UUIDs, collections, etc., with **no** LangChain `dumpd` / `lc` envelope.

### Error envelope

Runnable routes use `{"detail": "<message>"}` for error JSON (**FR-109**).

| Situation | HTTP status | Notes |
|-----------|-------------|--------|
| Non-`POST` on a registered `…/invoke` or `…/batch` path | **405** | `methods=["POST"]` only (**BR-105**). |
| Unknown runnable key | **404** | Literal per-key routes. |
| Missing / wrong `Content-Type`, invalid JSON, root not an object, missing `input` / `inputs`, or `inputs` not an array | **422** | `null` **is** valid for `input` (**BR-104**). |
| Uncaught `Exception` from `ainvoke` / `abatch` | **500** | No traceback in the response body; exception on `request.state.exception` for logging. |

### Metrics

`GET {base}/metrics` returns Prometheus text when the app was built with `create_runnable_app` (`Content-Type: text/plain; version=0.0.4; charset=utf-8`). `create_app` alone keeps an empty metrics body (spec 01).

**Families (metric base names):** `requests_total`, `request_duration_seconds`, `errors_total`, `request_size_bytes`, `response_size_bytes`. With non-empty `metrics_namespace`, exposition prefixes each with `{metrics_namespace}_`.

**Labels:** `runnable` (key), `endpoint` (`invoke` | `batch`) on the four primary families; `errors_total` also has `http_status_class` (`4xx` | `5xx`). Cardinality is bounded by `len(runnables) * 2` endpoints (**NFR-105**).

**Buckets (BR-202):** `(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)` plus `+Inf`.

**Probe isolation (BR-106):** `GET /health` and `GET /metrics` do not increment runnable metric families.

**Request size (BR-203):** when `Transfer-Encoding: chunked` is used, the `request_size_bytes` histogram is not observed for that request; the wide event omits `request_size_bytes` under the same rule.

Per-app registry: `app.state["metrics_registry"]` is a dedicated `CollectorRegistry`, not the process default (**FR-122**). Namespace string: `app.state["metrics_namespace"]` (**FR-123**).

### Logging

One structlog **INFO** event per request, name `http_request` (**FR-130**, **NFR-107**). The module `langgraph_runnable_server.logging` exposes `structlog.get_logger("langgraph_runnable_server")` only and does **not** call `structlog.configure` at import (**NFR-108**).

Run Uvicorn with **`access_log=False`** so access lines are not duplicated; rely on the wide event (**FR-131**, **FR-132**). The library does not attach stdlib handlers to `uvicorn.access` or `fastapi`.

### Cancellation

On cooperative cancellation (**BR-108**), `asyncio.CancelledError` is not swallowed: it propagates into the runnable. The handler sets `request.state.cancelled`; the wide event uses `http.status_code` **499** when possible (best-effort; logging must not raise into cancellation cleanup).

### Security boundary (NFR-111)

Hosts **must** place a reverse proxy or API gateway in front of this library to enforce **maximum request body size** and **timeouts**. The library does not cap JSON nesting depth or payload size. Cooperative `asyncio` cancellation follows **BR-108** when the client disconnects.

### Reference: spec field summary (NFR-110)

| Kind | Names / fields |
|------|------------------|
| **Metric names** | `requests_total`, `request_duration_seconds`, `errors_total`, `request_size_bytes`, `response_size_bytes` (each prefixed by `{metrics_namespace}_` when the namespace is non-empty). |
| **Metric labels** | `runnable`, `endpoint`; plus `http_status_class` on `errors_total`. |
| **Wide event (`http_request`)** | `http.method`, `http.route`, `http.status_code`, `duration_ms`, `instance_id`, `response_size_bytes` (always); optional `runnable`, `endpoint`, `request_size_bytes`, `trace_id`; on runnable failures `error.type`, `error.stack`. Runnable keys omitted on probes. |

## Prefix

`create_app(prefix=...)` sets the HTTP base path for both probes. Normalization follows **FR-011** in the spec (runs before any `FastAPI` object exists; invalid values raise `ValueError`).

| Input (after ASCII trim) | Effective `{base}` | Example probe paths |
|---------------------------|--------------------|------------------------|
| `"/"`, `""`, whitespace-only | *(empty)* | `/health`, `/metrics` |
| `"/api"`, `"/api/"` | `/api` | `/api/health`, `/api/metrics` |
| Contains `//` anywhere (e.g. `"/api///"`, `"/api//v1"`) | — | *rejected* (`ValueError`) |
| Missing leading `/` (e.g. `"api"`) | — | *rejected* |
| Non–URL-safe character in path (space, `?`, `#`, `<`, …) | — | *rejected* |

Trailing slashes are stripped after the `//` check, so `"/api/"` behaves like `"/api"`. A `//` substring is never collapsed: `"/api///"` is rejected because `//` appears before any trailing-slash normalization.

## Host-owned lifespan

Optional startup/shutdown work uses the standard FastAPI/Starlette lifespan contract (see **FR-003** / **FR-013** in the spec):

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from langgraph_runnable_server import create_app

# or, with a host-owned lifespan for startup/shutdown work:
@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: open connection pools, warm caches, etc.
    yield
    # shutdown: drain queues, close pools, etc.

app = create_app(prefix="/api", lifespan=lifespan)
# ASGI: uvicorn <host_module>:app  (owned by the host project)
```

## Versions

- **v0.2.0** — runnable HTTP surface, metrics, structured logging; see `CHANGELOG.md`.
- **v1.0** (spec 01) — probe app; VC-021 in `tests/interface/test_acceptance.py`.
- **v0.1** — default-prefix probes only; details in `CHANGELOG.md`.
