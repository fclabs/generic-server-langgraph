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

The second factory, `create_runnable_app`, composes `create_app` and validates runnable keys, prefixes, probe-path collisions, and the Prometheus metric namespace at factory time. **v0.2 (current development)** ships that validation and stores `app.state["metrics_namespace"]`; it does **not** yet register `POST …/invoke` or `POST …/batch` (those land in later iterations per [specs/02-runnable-support.md](specs/02-runnable-support.md)). This “factory-only” note is removed once the full surface is documented in iter 6 of the implementation plan.

```python
from langgraph_runnable_server import create_runnable_app

# Example only — runnable routes are not registered until a later release.
app = create_runnable_app(prefix="/agents", runnables={})
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
