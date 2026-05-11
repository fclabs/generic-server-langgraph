## Summary

This branch delivers **spec 02 — runnable HTTP support** (v0.2.0): a FastAPI factory and routes that expose LangChain/LangGraph `Runnable` instances with validation, structured errors, Prometheus metrics, structlog wide events per request, and broad interface/unit coverage.

## What changed

- **`create_runnable_app` factory** (`runnable_app.py`): keyword-only API; validates runnable map keys, metrics namespace, shared prefix, and overlap with the probe app; wires lifespan and prefix behavior from the host app.
- **HTTP surface**: `POST …/invoke` and `POST …/batch` per runnable key; JSON responses via FastAPI’s `jsonable_encoder`; correct **404** / **405** behavior; cooperative cancellation and safe **500** handling without leaking tracebacks.
- **Metrics**: per-app `CollectorRegistry` in app state; namespaced Prometheus families and histogram buckets aligned with the spec.
- **Logging**: `http_request` wide events with error metadata; no global structlog configuration at import.
- **Tests**: new runnable-focused interface tests (routes, errors, metrics, logging, cancellation, acceptance) plus unit tests for factory validation and metrics namespace rules; test suite split between unit and interface layers.
- **Docs & specs**: `specs/02-runnable-support.md`, implementation plan, README and CHANGELOG updates for **0.2.0**; dependency pins for `langchain-core`, `structlog`, and `prometheus-client`.

## Notes

- **Base branch:** `main`.
- You have **local uncommitted** changes (`pyproject.toml`, untracked `tests/integration/`). They are **not** part of this PR until committed and pushed.
