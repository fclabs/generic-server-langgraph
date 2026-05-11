# langgraph-runnable-server

Minimal FastAPI library exposing health and metrics endpoints under a configurable base path. See [specs/01-fastapi-server.md](specs/01-fastapi-server.md).

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

- **v0.1** — `GET /health` and `GET /metrics` on the default prefix, `app.state["instance_id"]`, and a no-op default lifespan. Details in `CHANGELOG.md`.
