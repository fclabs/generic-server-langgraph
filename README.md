# langgraph-runnable-server

Minimal FastAPI library exposing health and metrics endpoints under a configurable base path, plus a factory that will host LangChain/LangGraph `Runnable` HTTP endpoints. See [specs/01-fastapi-server.md](specs/01-fastapi-server.md) and [specs/02-runnable-support.md](specs/02-runnable-support.md).

## Acceptance

End-to-end public surface is covered by **VC-021** in a single test:

```bash
uv run pytest tests/interface/test_acceptance.py::test_full_public_surface -q
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

Only **GET** is defined on `/health` and `/metrics` (under `{base}`); any other HTTP method on those exact paths returns **404** (not 405). See **FR-014** / **BR-006** in the spec.

## Runnable HTTP surface

The second factory, `create_runnable_app`, composes `create_app`, runs factory-time validation (keys, prefix normalization, probe path overlap, `metrics_namespace`), stores `app.state["metrics_namespace"]`, and registers **one literal POST path per runnable key** for `invoke` and `batch`. See [specs/02-runnable-support.md](specs/02-runnable-support.md).

**Request bodies**

- `POST {prefix}/{key}/invoke` — JSON object with required `input` (any JSON value, including `null`) and optional `config` (passed to `Runnable.ainvoke`).
- `POST {prefix}/{key}/batch` — JSON object with required `inputs` (JSON array) and optional `config`. If `inputs` is `[]`, the server returns `[]` without calling `abatch`.

**Responses**

Successful calls return **200** with a JSON body produced by FastAPI’s `jsonable_encoder` (no LangChain `dumpd` envelope). See **BR-103** in spec 02.

**Error handling**

Runnable routes use a single JSON error envelope: `{"detail": "<message>"}`.

| Situation | HTTP status | Notes |
|-----------|-------------|--------|
| Non-`POST` on a registered `…/invoke` or `…/batch` path | **405** | Routes are registered with `methods=["POST"]` only (BR-105). |
| Unknown runnable key (no matching route) | **404** | Literal per-key registration. |
| Non-empty body without `Content-Type: application/json` (media type, case-insensitive) | **422** | Exact detail: `Content-Type must be application/json`. |
| Invalid JSON, JSON root not an object, missing `input` / `inputs`, or `inputs` not an array | **422** | `detail` explains the failure; `null` **is** allowed for `input`. |
| Uncaught exception from `ainvoke` / `abatch` | **500** | Same envelope; **no** traceback or stack frames in the response body (FR-109). The exception object is stored on `request.state.exception` for host logging (e.g. structlog in a later iteration). |

On cooperative **cancellation** (client disconnect / cancelled waiter), `asyncio.CancelledError` is not swallowed: it propagates to the runnable, and `request.state.cancelled` is set so logging can treat the request as cancelled (BR-108).

```python
from langgraph_runnable_server import create_runnable_app

class Echo:
    async def ainvoke(self, input, config=None):
        return {"echo": input}
    async def abatch(self, inputs, config=None):
        return [{"echo": i} for i in inputs]

app = create_runnable_app(
    prefix="/agents",
    runnables={"foo": Echo()},
)
```

With Uvicorn on port 8000:

```bash
curl -sS -X POST http://127.0.0.1:8000/agents/foo/invoke \
  -H 'Content-Type: application/json' \
  -d '{"input": {"q": "hello"}}'
# {"echo":{"q":"hello"}}
```

See [CHANGELOG.md](CHANGELOG.md) for version notes (v0.1: default-prefix health and metrics).

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

- **v1.0** (spec v1.8) — 2026-05-11: full spec implementation; see `CHANGELOG.md`.
- **v0.1** — `GET /health` and `GET /metrics` on the default prefix, `app.state["instance_id"]`, and a no-op default lifespan. Details in `CHANGELOG.md`.
