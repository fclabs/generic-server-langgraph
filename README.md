# langgraph-runnable-server

Minimal FastAPI library exposing health and metrics endpoints under a configurable base path. See [specs/01-fastapi-server.md](specs/01-fastapi-server.md).

## Quick start

```python
from langgraph_runnable_server import create_app

app = create_app()
```

Use any ASGI host to serve `app`; endpoint wiring is completed in later iterations of the implementation plan.
