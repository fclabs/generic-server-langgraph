# Implementation Plan: Generic FastAPI Server (Library)

**Spec:** [`specs/01-fastapi-server.md`](./01-fastapi-server.md) (v1.9, 2026-05-11)

**Summary:** Build the `langgraph-runnable-server` Python library exposing a single public callable `create_app(prefix, lifespan)` that returns a FastAPI app with `{base}/health` and `{base}/metrics` endpoints, per-app `instance_id` on `app.state`, and a configurable lifespan. Project tooling (uv, ruff, ty, pinned Python 3.12, committed `uv.lock`) is set up in iteration 1; functional surface is built incrementally across iterations 2–5; a final end-to-end acceptance test (VC-021) lands in iteration 6.

> **Review note:** This plan is generated against spec v1.9. The spec has not been formally run through `/review-spec` in this session; the changelog (1.0 → 1.9) shows iterative refinements resolving review action items, "Open Questions" is `None`, and every VC is concrete. If a formal review report is required by process, run `/review-spec` first and reconcile any `[MUST]` items before starting iteration 1.

---

## Iteration 1: Project scaffolding and tooling

**Goal**: Stand up the library package skeleton with pinned Python, committed lockfile, lint/typecheck tooling green, and an importable `create_app` stub — no functional endpoints yet.

**Scope**:
- Create `pyproject.toml` with:
  - `[project].name = "langgraph-runnable-server"`
  - `requires-python = ">=3.12"`
  - `[project].dependencies` with explicit lower bounds on `fastapi>=X.Y.Z` and `starlette>=X.Y.Z` (pick latest stable at implementation time; record exact pins in lockfile).
  - `[dependency-groups]` (or `[project.optional-dependencies].test`) with floored `pytest`, `httpx`, plus dev tooling `ruff`, `ty` (or documented mypy fallback per A-004).
- Create `.python-version` containing exactly `3.12` (no patch level, no range).
- Create `src/langgraph_runnable_server/` with:
  - `__init__.py` containing `from .<app_module> import create_app` and `__all__ = ["create_app"]` (exact list, no extras).
  - `py.typed` (empty file, PEP 561).
  - `<app_module>.py` (name implementation-defined; e.g. `app.py`) exporting a stub `create_app(prefix: str = "/", lifespan: Lifespan[FastAPI] | None = None) -> FastAPI` that returns a bare `FastAPI()` instance. Fully type-annotated. (No routes, no instance_id, no lifespan wiring yet — those land in iter 2+.)
  - `api/__init__.py`, `api/routes/__init__.py`, `api/routes/health.py` (empty placeholder router file), `api/routes/metrics.py` (empty placeholder router file).
  - `metrics/__init__.py`, `metrics/registry.py` (empty registry structure — module-level constant or empty list, not public API per FR-009).
- Create `tests/` with `tests/__init__.py` and `tests/interface/__init__.py`. Add a single smoke test `tests/interface/test_import.py` that asserts `from langgraph_runnable_server import create_app` and `langgraph_runnable_server.__all__ == ["create_app"]` (covers VC-015 import-surface check at the scaffold level).
- Add `ruff` and `ty` configuration (in `pyproject.toml` `[tool.ruff]`, `[tool.ty]` or equivalent) so lint/typecheck targets the `src/` and `tests/` trees.
- Run `uv lock` once to generate `uv.lock`; commit it. Verify `uv sync --frozen` succeeds.
- Add a brief `README.md` (or update existing) with one usage example: `from langgraph_runnable_server import create_app; app = create_app()` (matches FR-009 / FR-010 public surface).

**Out of scope** (deferred to later iterations):
- Any working endpoint (health/metrics return nothing in iter 1).
- `instance_id` wiring on `app.state`.
- Lifespan registration (default no-op or host-supplied).
- Prefix normalization and validation.
- Non-GET → 404 behavior.

**Success criteria**:
- VC-006 (Library package layout): file tree matches the spec's layout diagram; `py.typed` present; `__init__.py` re-exports `create_app` with `__all__ == ["create_app"]`. Verify by inspecting the tree (`ls -R src/langgraph_runnable_server/`) and by running the smoke test.
- VC-011 (Static typecheck clean): `uv run ty check` → exit code 0. (If `ty` is unavailable, `uv run mypy --strict src/langgraph_runnable_server/` per A-004 fallback.)
- VC-012 (Ruff clean): `uv run ruff check .` and `uv run ruff format --check .` → exit code 0.
- VC-014 (Single Python pin + uv-driven env): `.python-version` reads exactly `3.12`; `pyproject.toml` has `requires-python = ">=3.12"`; `uv sync --frozen` → exit code 0.
- VC-015 (partial — import surface only at this stage): `uv run python -c "import langgraph_runnable_server as p; assert p.__all__ == ['create_app']; from langgraph_runnable_server import create_app; assert callable(create_app)"` → exit code 0.
- VC-016 (`py.typed` present): `test -f src/langgraph_runnable_server/py.typed` → exit code 0.
- VC-020 (Dependency floors + lockfile authoritative): `pyproject.toml` declares `fastapi>=…` and `starlette>=…` with explicit lower bounds (no bare names); `uv.lock` exists at repo root; `uv lock --check` → exit code 0.
- Smoke test passes: `uv run pytest tests/interface/test_import.py -q` → all green.

**Documentation updates**:
- `README.md`: add a "Quick start" snippet showing the import + factory call (no host-side framework details — match spec §Data & Interfaces "Host service" example).
- Module docstrings: one-line docstring on `langgraph_runnable_server/__init__.py` describing the package's purpose and pointing to `create_app`.
- `pyproject.toml`: include `[project].description` referencing the spec ("Minimal FastAPI library exposing health and metrics endpoints under a configurable base path").

**Commit message** (draft):
- `feat(scaffold): initial package layout, tooling, and create_app stub`

---

## Iteration 2: Health, metrics, instance_id, default no-op lifespan (default prefix only)

**Goal**: Make `create_app()` (default `prefix="/"`) return a fully-working FastAPI app with `/health`, `/metrics`, per-app `instance_id`, and a registered no-op lifespan.

**Scope**:
- Wire `instance_id` on `app.state["instance_id"]` inside `create_app`:
  - Use `uuid.uuid4()` to generate a fresh UUID per `create_app` invocation.
  - Store as a string on `app.state` (FastAPI's `state` is a `State` object; `app.state.instance_id = "..."` and `app.state["instance_id"]` should both work — the spec normatively uses subscript access in VC-001, VC-021, so ensure both work or document the canonical access. Implementation note: FastAPI's `State` supports attribute access; the spec's `app.state["instance_id"]` in VC-021 implies subscript-style access — verify and, if needed, store in a way that supports both, e.g. set `app.state.instance_id` AND ensure `app.state["instance_id"]` resolves. **Flag if FastAPI's `State` does not support subscript out of the box** — if it doesn't, either (a) wrap with a custom dict-like state, (b) raise the issue with the spec author, or (c) document the chosen access pattern in the implementation. Per FR-001 the *normative contract* is "`app.state` contains a documented key" and VC-021 uses subscript — prefer to make subscript work to satisfy the literal VC.).
- Always wire a `lifespan` on the `FastAPI(...)` constructor. In this iteration only the no-op default is needed:
  ```python
  @asynccontextmanager
  async def _default_lifespan(app: FastAPI):
      yield
  ```
  Pass `lifespan=_default_lifespan` when the caller did not supply one. (Host-supplied lifespan support comes in iter 4, but the wiring path must already accept it being `None`.)
- Implement `api/routes/health.py`:
  - APIRouter (or plain function on a router) for `GET /health` (no prefix on the router itself in this iter; the router is `include_router`'d at the app level without a prefix).
  - Response: status 200, `Content-Type: text/plain` (or `text/plain; charset=utf-8` per A-006), body **exactly** the two ASCII bytes `b"ok"` — no trailing newline. Use `Response(content=b"ok", media_type="text/plain")` to avoid framework-injected newlines.
  - Handler must be a pure function — no external I/O (BR-002).
- Implement `api/routes/metrics.py`:
  - `GET /metrics` returning status 200 with zero-length body. Use `Response(content=b"", status_code=200)` or equivalent (avoid any default Prometheus body).
- Register both routers in `create_app` at the app level (no prefix passed to `include_router` in iter 2 — prefix logic comes in iter 3).
- Tests under `tests/interface/test_health_and_metrics.py`:
  - VC-001: `app = create_app(); assert isinstance(app.state["instance_id"], str) and len(app.state["instance_id"]) > 0`; UUID-v4-ish (length 36 with dashes, or document equivalent).
  - VC-002: `a = create_app(); b = create_app(); assert a.state["instance_id"] != b.state["instance_id"]`.
  - VC-003a: `app = create_app(); assert app.router.lifespan_context is not None`; `with TestClient(app): pass` completes without raising.
  - VC-004 (default only): `TestClient(create_app()).get("/health")` → 200, `.content == b"ok"` (length 2, no trailing newline), `Content-Type` starts with `text/plain`.
  - VC-005 (default only): `TestClient(create_app()).get("/metrics")` → 200, `.content == b""`.
  - VC-008: byte-exact equality `response.content == b"ok"` (already covered by VC-004 — keep an explicit byte-length assertion `len(response.content) == 2`).
  - VC-009: patch `socket.socket`, `socket.socket.connect`, `socket.socket.connect_ex`, `httpx.Client.send`, `httpx.AsyncClient.send`, and `urllib.request.urlopen` with replacements that raise `AssertionError("health must not perform external I/O: <call>")`. Issue `GET /health` against `TestClient(create_app())` and assert 200 + `b"ok"` with no patched primitive invoked. (Prefixed app variant added in iter 3.)
  - VC-010: import `langgraph_runnable_server.metrics.registry` from inside the test and assert its empty-registry shape (e.g. `assert registry.METRICS == ()` or whatever the chosen structure is). Combined with VC-005's empty-body check.

**Out of scope** (deferred):
- `prefix` argument behavior beyond the default `"/"` value (iter 3).
- Host-supplied lifespan acceptance (iter 4).
- Non-GET → 404 enforcement (iter 5).

**Success criteria**:
- VC-001, VC-002 (instance_id present, non-empty, distinct across apps): unit tests pass.
- VC-003a (default lifespan registered): test passes.
- VC-004, VC-005, VC-008 (default-prefix endpoints work, body byte-exact, content-type correct): tests pass.
- VC-009 (no external I/O in health) for default app: passes; the prefixed-app half is added in iter 3.
- VC-010 (registry empty): passes.
- Full suite: `uv run pytest tests/ -q` → all green.
- Tooling still clean: `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check` → exit 0.
- NFR-003 sanity: `time uv run pytest tests/interface/ -q` completes in well under 30s.

**Documentation updates**:
- `README.md`: extend "Quick start" with an example `curl localhost:.../health` returning `ok` (illustrative — host owns the server; mark as illustrative per spec §Data & Interfaces "Host service" note).
- Inline docstring on `create_app` summarizing the contract: returns a `FastAPI` instance with `/health` and `/metrics` mounted, `app.state["instance_id"]` set, and a no-op lifespan. Reference FR-001, FR-003, FR-004, FR-005.
- Add a `CHANGELOG.md` entry or section in `README.md` noting "v0.1: default-prefix endpoints implemented".

**Commit message** (draft):
- `feat: health, metrics, instance_id, and default lifespan on create_app()`

---

## Iteration 3: Prefix support with normalization and validation

**Goal**: Accept the `prefix` argument on `create_app`, normalize per FR-011, validate per the RFC-3986-pchar rule, and reject malformed inputs with `ValueError` before any `FastAPI` is constructed.

**Scope**:
- Implement a `_normalize_prefix(prefix: str) -> str` helper (private; not exported) that performs the FR-011 algorithm in order:
  1. Trim leading/trailing ASCII whitespace.
  2. If `"//"` substring present anywhere → raise `ValueError("prefix must not contain '//'")`.
  3. If trimmed string is empty → return `""` (caller will treat as root, no base segment).
  4. If non-empty and does not start with `/` → raise `ValueError("prefix must start with '/'")`.
  5. Strip trailing `/` until the working value is `"/"` or no longer ends with `/`. If after stripping it is `"/"`, return `""` (root → no base). Otherwise the working value is the non-root base.
  6. For non-root results: validate every character is `/` OR an RFC 3986 `pchar` (`unreserved` = `A-Z a-z 0-9 - . _ ~`; `sub-delims` = `! $ & ' ( ) * + , ; =`; plus `:`, `@`, and `pct-encoded` triplets `%HH`). Reject ASCII whitespace, `?`, `#`, `<`, `>`, etc. → raise `ValueError("prefix contains invalid character: ...")`.
  - Return the empty string for the root case, or the validated non-root base (e.g. `/api`).
- Call `_normalize_prefix` at the **top** of `create_app` (before `FastAPI(...)` is instantiated). If it raises, the caller sees `ValueError` and no FastAPI app exists.
- Pass the normalized base to `app.include_router(router, prefix=<base>)` for both health and metrics routers. When base is empty, omit the prefix (or pass `""`); routes resolve to `/health` and `/metrics` as before.
- Extend `tests/interface/test_health_and_metrics.py` (or add `tests/interface/test_prefix.py`):
  - VC-004 (prefixed): `TestClient(create_app(prefix="/api")).get("/api/health")` → 200, `b"ok"`, correct content-type.
  - VC-005 (prefixed): `TestClient(create_app(prefix="/api")).get("/api/metrics")` → 200, empty body.
  - VC-009 (prefixed half): patch network primitives, hit `/api/health`, assert clean.
  - VC-017: parametrized — `create_app()` and `create_app(prefix="")` and `create_app(prefix="   ")` all serve `/health`+`/metrics`; `create_app(prefix="/api")` serves `/api/health`+`/api/metrics`.
  - VC-018: `create_app(prefix="/api/")` and `create_app(prefix="/api///")` both behave like `prefix="/api"` (no `//` after normalization — note: `"/api///"` contains `"//"` → must **raise** per FR-011 step 2. Re-read: step 2 rejects any `"//"` substring *before* trailing-slash stripping; so `"/api/"` is OK and normalizes to `/api`, but `"/api//"` is rejected. Test both cases: `"/api/"` accepted, `"/api//"` rejected.).
  - VC-019: parametrized rejections, each asserting `ValueError`:
    - `create_app(prefix="api")` (missing leading `/`).
    - `create_app(prefix="//")` (contains `//`).
    - `create_app(prefix="/api//v1")` (contains `//`).
    - `create_app(prefix="/a b")` (ASCII space).
    - `create_app(prefix="/a?b")` (literal `?`).
    - `create_app(prefix="/a#b")` (literal `#`).
    - `create_app(prefix="/a<b")` (non-pchar character).
  - Verify in tests that no `FastAPI` instance is constructed for the rejection cases — e.g. wrap in `with pytest.raises(ValueError)` and confirm no side effects.

**Out of scope** (deferred):
- Host-supplied lifespan (iter 4).
- Non-GET probes returning 404 (iter 5).

**Success criteria**:
- VC-004 and VC-005 now hold for **both** default and prefixed apps.
- VC-009 hits both default and prefixed health endpoints with patches active and stays green.
- VC-017 (prefix → `{base}` URL mapping) passes.
- VC-018 (trailing slash normalized) passes.
- VC-019 (invalid prefixes raise `ValueError` pre-construction) passes for every listed example.
- Full suite green: `uv run pytest tests/ -q`.
- Tooling still clean: `uv run ruff check .`, `uv run ty check`, `uv run ruff format --check .` → exit 0.

**Documentation updates**:
- `README.md`: add a "Prefix" subsection documenting accepted/rejected forms, with a short table mirroring FR-011 (e.g., `"/"` → root, `""` → root, `"/api"` → `/api/health`, `"/api/"` → normalized to `/api`, `"//"` → ValueError).
- Docstring on `create_app`: enumerate the prefix rules at a high level and link/refer to FR-011 in the spec.

**Commit message** (draft):
- `feat: prefix argument with FR-011 normalization and pchar validation`

---

## Iteration 4: Host-supplied lifespan

**Goal**: Accept a `Lifespan[FastAPI] | None` argument on `create_app`, pass it through verbatim to FastAPI when non-None, and continue to install the no-op default otherwise.

**Scope**:
- Adjust `create_app` signature to make the lifespan plumbing explicit:
  ```python
  from starlette.types import Lifespan
  def create_app(prefix: str = "/", lifespan: Lifespan[FastAPI] | None = None) -> FastAPI:
      ...
      if lifespan is None:
          app = FastAPI(lifespan=_default_lifespan)
      else:
          app = FastAPI(lifespan=lifespan)  # verbatim, no wrapping (FR-003)
      ...
  ```
- Do **not** compose, chain, or wrap the host-supplied lifespan with the library's no-op lifespan — FR-003 requires the host's lifespan to wholly replace the default.
- Keep `__all__ = ["create_app"]` — do **not** re-export `Lifespan` (FR-009, FR-010 note).
- Tests:
  - VC-003b (host-supplied lifespan runs startup AND shutdown):
    ```python
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state["startup_ran"] = True
        yield
        app.state["shutdown_ran"] = True

    app = create_app(lifespan=lifespan)
    with TestClient(app) as client:
        client.get("/health")
        assert app.state["startup_ran"] is True
        assert "shutdown_ran" not in app.state  # or: not hasattr / not yet set
    assert app.state["shutdown_ran"] is True
    ```
    (Adjust the "not yet set" assertion to fit whichever access pattern the implementation chose in iter 2 for `app.state` subscript support.)
  - VC-003a still passes for `create_app()` with no `lifespan` argument.
  - Add one negative test: `create_app(lifespan=lifespan)` with a host lifespan that raises during startup propagates the exception out of `TestClient(app).__enter__` — this verifies the library does not silently swallow lifespan errors (also defends FR-003's "verbatim" wording).

**Out of scope** (deferred):
- Non-GET → 404 enforcement (iter 5).

**Success criteria**:
- VC-003b passes (startup body observable inside `with TestClient(app)`, shutdown body observable after exit).
- VC-003a still passes for the default-lifespan case.
- The library does not wrap or compose the host lifespan — verify via inspection that `app.router.lifespan_context` is `lifespan` (the function/object the host passed) when a lifespan is supplied. (Implementation note: `FastAPI` may wrap the callable into a context manager internally; the assertion should be that the host's startup/shutdown bodies actually run, which VC-003b checks. Drop the identity check if FastAPI's internals make it brittle.)
- Full suite green; tooling clean.

**Documentation updates**:
- `README.md`: add the "host-owned lifespan" example from spec §Data & Interfaces verbatim or paraphrased.
- Docstring on `create_app`: document the `lifespan` parameter, point out it's keyword-recommended and passed through unchanged; reference FR-003 and FR-013.

**Commit message** (draft):
- `feat: accept optional host-supplied lifespan and pass through verbatim`

---

## Iteration 5: Non-GET methods return 404 on probe paths

**Goal**: Ensure any non-`GET` HTTP method against `{base}/health` or `{base}/metrics` returns 404 (not 405), per FR-014 / BR-006.

**Scope**:
- FastAPI's default behavior when a route is registered for a single method and a different method is requested is **405 Method Not Allowed** (Starlette routing). The spec **forbids** 405 on these paths and requires 404.
- Implementation options (pick one in the iteration; flag the choice in the docstring/PR description):
  1. **Per-route override**: register the probe routes such that other methods produce 404. One way: use `add_route` with a custom handler that inspects the method and returns 404 for non-GET; or include a catch-all route on `{base}/health` and `{base}/metrics` that returns 404 for non-GET. (Risk: ordering matters.)
  2. **Custom 405 → 404 handler**: register an exception handler / middleware that transforms 405 responses on `{base}/health` and `{base}/metrics` into 404. (Cleaner; less route-ordering risk.)
  3. **Register routes for all methods**: declare the GET handler with `methods=["GET"]` AND register a fallback handler on the same path with `methods=["HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]` that returns 404.
- Whichever approach is taken, the behavior must hold for both default and prefixed apps.
- Tests (in `tests/interface/test_methods.py` or extending existing files):
  - VC-022: parametrized over `app = create_app()` and `app = create_app(prefix="/api")`, and over at least `HEAD` and `POST` (the spec requires "at least two distinct non-GET methods"; include both, and consider adding `PUT`, `PATCH`, `DELETE`, `OPTIONS` for thoroughness):
    ```python
    for method in ["HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]:
        for path in ["/health", "/metrics"]:  # adjust for prefix
            response = client.request(method, path)
            assert response.status_code == 404, (method, path, response.status_code)
    ```
  - Ensure `GET /health` and `GET /metrics` still work (regression check).

**Out of scope** (deferred):
- VC-021 acceptance test (iter 6).

**Success criteria**:
- VC-022 passes: every non-GET method on `/health`, `/metrics`, `/api/health`, `/api/metrics` returns **exactly 404**. No 405, no 200.
- GET paths still pass (VC-004, VC-005 regression).
- Full suite green; tooling clean.

**Documentation updates**:
- `README.md`: add a one-line note that "only GET is supported on /health and /metrics; other methods return 404".
- Docstring on `create_app` or the route module: reference FR-014 / BR-006.

**Commit message** (draft):
- `feat: return 404 (not 405) for non-GET requests on probe paths`

---

## Iteration 6: End-to-end acceptance test and final verification

**Goal**: Land the single-test VC-021 acceptance check that exercises the full public surface in one run, then verify the entire spec is satisfied.

**Scope**:
- Add `tests/interface/test_acceptance.py` with **one** test function `test_full_public_surface()` that implements VC-021 exactly as written in the spec:
  ```python
  from fastapi.testclient import TestClient
  from langgraph_runnable_server import create_app

  def test_full_public_surface():
      default_app = create_app()
      prefixed_app = create_app(prefix="/api")

      default_id_before = default_app.state["instance_id"]
      prefixed_id_before = prefixed_app.state["instance_id"]

      # 1. Non-empty strings
      assert isinstance(default_id_before, str) and len(default_id_before) > 0
      assert isinstance(prefixed_id_before, str) and len(prefixed_id_before) > 0

      # 2. Distinct
      assert default_id_before != prefixed_id_before

      # 3. Default app endpoints + lifespan
      with TestClient(default_app) as client:
          h = client.get("/health")
          m = client.get("/metrics")
          assert h.status_code == 200
          assert h.content == b"ok"
          assert h.headers["content-type"].startswith("text/plain")
          assert m.status_code == 200
          assert m.content == b""
          assert default_app.state["instance_id"] == default_id_before

      # 4. Prefixed app + un-prefixed paths are 404
      with TestClient(prefixed_app) as client:
          h = client.get("/api/health")
          m = client.get("/api/metrics")
          assert h.status_code == 200
          assert h.content == b"ok"
          assert m.status_code == 200
          assert m.content == b""
          assert client.get("/health").status_code == 404
          assert client.get("/metrics").status_code == 404

      # 5. Instance ID stable after all requests
      assert default_app.state["instance_id"] == default_id_before
      assert prefixed_app.state["instance_id"] == prefixed_id_before

      # 6. __all__ discipline
      import langgraph_runnable_server as p
      assert p.__all__ == ["create_app"]
  ```
- Confirm the test must remain a **single function** (per spec note on VC-021) so a failure is one red flag.
- Run the full suite end-to-end and measure wall time for NFR-003 (VC-013) — target < 30s.
- Run the full tooling matrix and confirm green:
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run ty check`
  - `uv run pytest tests/ -q`
  - `uv sync --frozen` (lockfile authoritative — VC-020)
- Cross-check every VC against the implementation (see Final Verification table below).

**Out of scope**:
- Nothing — this is the final iteration; the library should be host-ready after this.

**Success criteria**:
- VC-021 passes as a single test.
- VC-007 (interface tests green): `uv run pytest tests/interface/ -q` → all green.
- VC-013 (test duration): wall time for `uv run pytest tests/interface/ -q` < 30s on a typical dev machine.
- Every VC in the Final Verification table below is checkable and green.
- No regressions in any prior iteration's success criteria.

**Documentation updates**:
- `README.md`: top-of-file "Acceptance" section that references VC-021 and how to run it (`uv run pytest tests/interface/test_acceptance.py::test_full_public_surface -q`).
- `CHANGELOG.md` (or README changelog section): mark "v1.0: spec v1.9 fully implemented" with date.
- Re-read the spec's §Changelog (entries 1.0 → 1.9) and confirm the implementation matches the **current** (1.9) behavior in every place. Note any discrepancies in the PR description, not the code.

**Commit message** (draft):
- `feat: end-to-end acceptance test (VC-021) and final docs`

---

## Final Verification

Cross-check every requirement from spec v1.9 against the iteration that implements it and how to verify:

| Requirement | VC(s)         | Iteration(s) | Verification                                                                                              |
|-------------|---------------|--------------|-----------------------------------------------------------------------------------------------------------|
| FR-001      | VC-001        | Iter 2       | `pytest` asserts `app.state["instance_id"]` is non-empty string and stable for the app's lifetime.        |
| FR-002      | VC-002        | Iter 2       | `pytest` asserts two `create_app()` calls in one process produce distinct UUID-v4 strings.                |
| FR-003      | VC-003a, VC-003b | Iter 2, 4 | Iter 2 wires the no-op default (`app.router.lifespan_context is not None`); iter 4 verifies host-supplied lifespan runs startup AND shutdown via `TestClient` context. |
| FR-004      | VC-004, VC-008 | Iter 2, 3   | `GET {base}/health` → 200, body `b"ok"` (length 2, no trailing newline), `text/plain` content-type, for default and prefixed apps. |
| FR-005      | VC-005        | Iter 2, 3    | `GET {base}/metrics` → 200, empty body, for default and prefixed apps.                                    |
| FR-006      | VC-006        | Iter 1       | File-tree inspection: `src/langgraph_runnable_server/` has `__init__.py` (`__all__ == ["create_app"]`), `py.typed`, app module, `api/routes/{health,metrics}.py`, `metrics/registry.py`. |
| FR-007      | VC-006        | Iter 1       | `pyproject.toml` `[project].name == "langgraph-runnable-server"`; import name `langgraph_runnable_server` matches under PEP 503. |
| FR-008      | VC-007, VC-022, VC-003b | Iter 2, 3, 4, 5, 6 | `uv run pytest tests/interface/ -q` → all green; covers default, prefixed, non-GET 404, and host lifespan. |
| FR-009      | VC-015        | Iter 1 (stub), Iter 6 (final assert in VC-021) | `import langgraph_runnable_server as p; assert p.__all__ == ["create_app"]`. |
| FR-010      | VC-015, VC-017 | Iter 1 (signature), Iter 3 (prefix), Iter 4 (lifespan) | Signature `create_app(prefix: str = "/", lifespan: Lifespan[FastAPI] \| None = None) -> FastAPI`; default + prefix + lifespan all exercised. |
| FR-011      | VC-017, VC-018, VC-019 | Iter 3 | Parametrized tests cover accepted forms, trailing-slash normalization, and every rejection case. |
| FR-012      | VC-016        | Iter 1       | `test -f src/langgraph_runnable_server/py.typed` and `uv run ty check` clean (NFR-001 / VC-011). |
| FR-013      | VC-003b       | Iter 4       | Host-supplied `Lifespan[FastAPI]` accepted and run verbatim; startup + shutdown observable in `TestClient` context. |
| FR-014      | VC-022        | Iter 5       | Non-GET methods (HEAD, POST, …) on `{base}/health` and `{base}/metrics` return 404 for default + prefixed apps. |
| BR-001      | VC-008        | Iter 2       | Byte-equal `response.content == b"ok"`, `len == 3`, no trailing newline. |
| BR-002      | VC-009        | Iter 2 (default), Iter 3 (prefixed half) | Network primitives (`socket.socket`, `connect`, `httpx.Client.send`, `httpx.AsyncClient.send`, `urllib.request.urlopen`) patched; `GET /health` and `GET /api/health` succeed without touching any. |
| BR-003      | VC-010, VC-005 | Iter 2      | `metrics.registry` import shows empty structure; `GET /metrics` body is zero-length. |
| BR-004      | VC-017        | Iter 3       | Prefix sets `{base}`; default and `""` map to root; `"/api"` maps to `/api/...`. |
| BR-005      | VC-018, VC-019 | Iter 3      | Trailing slashes stripped; `//` rejected; result is `"/"` or absolute path without trailing `/`. |
| BR-006      | VC-022        | Iter 5       | Same as FR-014. |
| NFR-001     | VC-011        | Iter 1 (set up), every iter (kept green) | `uv run ty check` → exit 0. |
| NFR-002     | VC-012        | Iter 1 (set up), every iter (kept green) | `uv run ruff check .` and `uv run ruff format --check .` → exit 0. |
| NFR-003     | VC-013        | Iter 6       | `time uv run pytest tests/interface/ -q` < 30s. |
| NFR-004     | VC-014        | Iter 1       | `.python-version` == `3.12`; `pyproject.toml` `requires-python = ">=3.12"`; `uv sync --frozen` exit 0. |
| NFR-005     | VC-020        | Iter 1       | `pyproject.toml` declares `fastapi>=…`, `starlette>=…` floors; `uv.lock` committed; `uv lock --check` exit 0. |
| (acceptance)| VC-021        | Iter 6       | Single-function test covering FR-001/002/003/004/005/009/010/011/014 + BR-001/003/004/005/006 + A-001 in one run. |

**Final acceptance test**: from a clean clone on Python 3.12, run

```sh
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest tests/ -q
uv run pytest tests/interface/test_acceptance.py::test_full_public_surface -q
```

All six commands must exit 0. The last command alone (VC-021) is the spec's single-test acceptance gate.
