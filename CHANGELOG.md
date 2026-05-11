# Changelog

## v0.2.0 — 2026-05-11

Runnable HTTP surface for LangChain/LangGraph `Runnable` instances, composed on top of the spec-01 probe app.

- **Iteration 1 — factory:** `create_runnable_app` (keyword-only) validates plain-`dict` `runnables`, key regex (**BR-107**), `metrics_namespace` (**FR-123**), shared prefix normalization (**FR-111**), and strict probe path overlap (**FR-108**). Exposes `app.state["metrics_namespace"]`.
- **Iteration 2 — routes:** literal `POST …/invoke` and `POST …/batch` per key; `jsonable_encoder` responses (**BR-103**); empty `inputs` short-circuit (**BR-102**); host `lifespan` and `create_app_prefix` forwarded unwrapped (**FR-112**).
- **Iteration 3 — errors:** `Content-Type` and JSON shape validation with `{"detail": …}` (**BR-104**, **BR-109**); runnable `Exception` → 500 without traceback to clients (**FR-109**); **405** / **404** discipline (**BR-105**); cooperative cancellation (**BR-108**).
- **Iteration 4 — metrics:** per-app `CollectorRegistry` at `app.state["metrics_registry"]` (**FR-122**); five namespaced Prometheus families with **BR-202** buckets; conditional `GET /metrics` exposition (**FR-120**); runnable-only instrumentation with **BR-106** and **BR-203** request-size rules.
- **Iteration 5 — logging:** one structlog `http_request` wide event per request (**FR-130**, **BR-301**, **NFR-107**); `error.type` / `error.stack` on failures (**VC-118**); **499** on cancellation; `logging` module without `structlog.configure` at import (**NFR-108**); no stdlib access handlers (**FR-131**, **FR-132**).
- **Iteration 6 — acceptance & docs:** VC-120 end-to-end test `tests/interface/test_runnable_acceptance.py::test_full_runnable_surface`; README runnable sections, NFR-110 field summary, NFR-111 security boundary; version **0.2.0**.

**Dependencies:** `langchain-core`, `structlog`, `prometheus-client` (pinned floors in `pyproject.toml` / `uv.lock`).

## v1.0 — 2026-05-11

- **Spec v1.8 fully implemented** (see [specs/01-fastapi-server.md](specs/01-fastapi-server.md)): library is host-ready; VC-021 acceptance test in `tests/interface/test_acceptance.py`.

## v0.1

- Default-prefix `GET /health` (body `ok`, `text/plain`) and `GET /metrics` (empty body) on `create_app()`.
- Per-app `instance_id` on `app.state` and a no-op default async lifespan.
