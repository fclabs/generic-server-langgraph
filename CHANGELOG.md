# Changelog

## v0.1

- Default-prefix `GET /health` (body `ok`, `text/plain`) and `GET /metrics` (empty body) on `create_app()`.
- Per-app `instance_id` on `app.state` and a no-op default async lifespan.
