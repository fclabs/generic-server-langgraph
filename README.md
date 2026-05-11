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

## Versions

- **v0.1** — `GET /health` and `GET /metrics` on the default prefix, `app.state["instance_id"]`, and a no-op default lifespan. Details in `CHANGELOG.md`.
