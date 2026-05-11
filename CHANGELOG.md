# Changelog

## v0.2 (unreleased)

- **`create_runnable_app` factory (iteration 1):** keyword-only API composing `create_app`; factory-time validation for plain-`dict` `runnables`, runnable key regex, `metrics_namespace` type and identifier regex, shared prefix normalization (`_prefix` module), and strict path-overlap checks vs probe `/health` and `/metrics` paths. Sets `app.state["metrics_namespace"]`.
- **Runnable routes (iteration 2, happy path):** `POST {runnables_base}/{key}/invoke` and `POST {runnables_base}/{key}/batch` registered per key with literal paths; `jsonable_encoder` for responses; empty `inputs` returns `[]` without `abatch`; malformed JSON → **422**.
- **Runnable validation & errors (iteration 3):** `Content-Type` guard for non-empty bodies; JSON parse and shape validation with `{"detail": "..."}` for all **422**; runnable `Exception` → **500** with the same envelope (no traceback to client) and `request.state.exception`; **405** on non-POST; cooperative cancellation sets `request.state.cancelled` and re-raises `CancelledError`.
- **Dependencies:** `langchain-core`, `structlog`, and `prometheus-client` added with explicit lower bounds (see `pyproject.toml`).

## v1.0 — 2026-05-11

- **Spec v1.8 fully implemented** (see [specs/01-fastapi-server.md](specs/01-fastapi-server.md)): library is host-ready; VC-021 acceptance test in `tests/interface/test_acceptance.py`.

## v0.1

- Default-prefix `GET /health` (body `ok`, `text/plain`) and `GET /metrics` (empty body) on `create_app()`.
- Per-app `instance_id` on `app.state` and a no-op default async lifespan.
