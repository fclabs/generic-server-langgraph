# Changelog

## v1.0 — 2026-05-11

- **Spec v1.8 fully implemented** (see [specs/01-fastapi-server.md](specs/01-fastapi-server.md)): library is host-ready; VC-021 acceptance test in `tests/interface/test_acceptance.py`.

## v0.1

- Default-prefix `GET /health` (body `ok`, `text/plain`) and `GET /metrics` (empty body) on `create_app()`.
- Per-app `instance_id` on `app.state` and a no-op default async lifespan.
