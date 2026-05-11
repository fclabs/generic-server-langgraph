# Implementation Plan: LangGraph Runnable HTTP Surface, Metrics, and Request Logging

**Spec:** [`specs/02-runnable-support.md`](./02-runnable-support.md) (v1.2, 2026-05-11)

**Summary:** Add a second public factory `create_runnable_app` that composes the existing `create_app` (spec 01) with HTTP routes for each configured LangChain/LangGraph `Runnable` (`POST {prefix}/{key}/invoke`, `POST {prefix}/{key}/batch`), a per-app Prometheus registry exposed at `{probe_base}/metrics`, and exactly one structlog wide event per HTTP request. The work is split into six iterations: (1) dependencies + factory skeleton with argument validation, (2) runnable route happy path + serialization + lifespan passthrough, (3) error handling and request-body validation paths, (4) Prometheus metrics, (5) structlog wide events + uvicorn access-log discipline, and (6) end-to-end acceptance test (VC-120) plus final documentation polish.

> **Review note:** Spec v1.2 has not been put through `/review-spec` in this session. The changelog (1.0 → 1.2) shows iterative refinements that resolve review action items; every requirement is paired with at least one concrete VC; "Open Questions" contains only two non-blocking items (OQ-001 Unicode keys, OQ-002 per-item batch configs) both explicitly out of scope for v1. If process requires a formal `READY` verdict, run `/review-spec` first and reconcile any `[MUST]` items before starting iteration 1.

> **Starting state:** Spec 01 is fully implemented. `src/langgraph_runnable_server/` has `app.py` with `create_app(prefix, lifespan)`, `_normalize_prefix`, the non-GET-on-probes middleware, and probe routers under `api/routes/{health,metrics}.py`. `__all__ == ["create_app"]`. Existing interface tests under `tests/interface/`: `test_import.py`, `test_health_and_metrics.py`, `test_methods.py`, `test_acceptance.py`. Dependencies pinned: `fastapi>=0.136`, `starlette>=1.0`, dev: `pytest`, `httpx`, `ruff`, `ty`. Lockfile committed.

---

## Iteration 1: Dependencies, factory skeleton, and argument validation

**Goal**: Add the runtime dependencies, introduce `create_runnable_app` with its full keyword-only signature and all factory-time validation (prefix normalization, key regex, non-`dict` rejection, path-collision check, namespace validation), but **no** runnable routes yet. The factory composes `create_app` and returns a working probe-only app whose `__all__` is extended.

**Scope**:
- `pyproject.toml`: add `langchain-core>=…`, `structlog>=…`, `prometheus-client>=…` to `[project].dependencies` with explicit lower-bound specifiers set to the latest stable release at implementation time (per spec 01 NFR-005 policy and spec 02 NFR-104). Update `[project].description` to mention the runnable HTTP surface.
- Run `uv lock` once; commit `uv.lock`. Verify `uv sync --frozen` succeeds.
- Create new module `src/langgraph_runnable_server/runnable_app.py` (or equivalent name) exporting `create_runnable_app` with this exact signature (FR-110):
  ```python
  def create_runnable_app(
      *,
      prefix: str,
      runnables: dict[str, Runnable],
      create_app_prefix: str = "/",
      lifespan: Lifespan[FastAPI] | None = None,
      metrics_namespace: str = "langgraph_runnable_server",
  ) -> FastAPI: ...
  ```
  - All parameters keyword-only (the leading `*,`).
  - `Runnable` imported from `langchain_core.runnables` per A-002 / NFR-104.
- Implement factory-time validation, **before any `FastAPI` instance is returned**, in this order:
  1. **`runnables` type check (FR-105)**: `isinstance(runnables, dict)` — non-`dict` mappings (e.g. `collections.UserDict`, `types.MappingProxyType`) raise `TypeError`.
  2. **`metrics_namespace` type+regex check (FR-123)**: must be `str` (else `TypeError`); empty string is allowed; non-empty must match `^[a-zA-Z_][a-zA-Z0-9_]*$` (else `ValueError`). `:` explicitly rejected (covered by regex). Whitespace rejected.
  3. **Key regex check (FR-107, BR-107)**: every key in `runnables` must match `^[A-Za-z0-9._-]{1,64}$`. Reject `""`, length ≥ 65, slash, whitespace, `$`, etc. with `ValueError`. Boundaries: length 1 and length 64 are accepted; length 0 and length 65 are rejected.
  4. **Runnable `prefix` normalization (FR-111)**: reuse the same `_normalize_prefix` from spec 01 (`app.py`) — extract it to a small shared module (e.g. `src/langgraph_runnable_server/_prefix.py`) or import from `app.py` if the existing private helper is acceptably reusable. Same trim / `//` rejection / leading-`/` / trailing-slash strip / `pchar` validation.
  5. **Path collision check (FR-108)**: compute `{runnables_base}` from the normalized runnable `prefix` and `{probe_base}` from `create_app_prefix`. For every key `k`, build the **full normalized** paths `{runnables_base}/{k}/invoke` and `{runnables_base}/{k}/batch` and compare against `{probe_base}/health` and `{probe_base}/metrics`. On any equality, raise `ValueError` with a message that names the colliding path (e.g. `"runnable path '/health/x/invoke' collides with probe path '/health'"`). The check Must compare **full paths**, not prefixes — but note the spec also requires rejection when a runnable route would shadow a probe path (e.g. `prefix="/health"` with key `"x"` produces `/health/x/invoke` and the registered probe is `/health`; these are not equal but the spec requires this case to fail too via the **path collision check**). Read FR-108 + VC-114 carefully and implement the stricter interpretation: any normalized `{runnables_base}/{k}/{invoke|batch}` whose prefix is `{probe_base}/health` or `{probe_base}/metrics` (or vice versa) is a collision. **Flag the literal "full path" wording in FR-108 vs the VC-114 example (`prefix="/health"`)** — implement the safer reading (overlap check), and note this in the module docstring.
- After validation passes, call `create_app(prefix=create_app_prefix, lifespan=lifespan)` and return that app **without** registering any runnable routes (those land in iter 2). The runnable `prefix` must still be normalized so failures show up here, not later. Forward `lifespan` and `create_app_prefix` verbatim (FR-112) — no wrapping or composition.
- Update `src/langgraph_runnable_server/__init__.py`:
  - `from .runnable_app import create_runnable_app`
  - `__all__ = ["create_app", "create_runnable_app"]` (extension of spec 01 FR-009 per "Amendments to spec 01" and FR-112).
  - Update module docstring to mention the new factory.
- Add tests under `tests/interface/`:
  - `test_runnable_factory.py`:
    - VC-106 (FR-107, BR-107): for keys `"a/b"`, `""`, `"a"*65`, `"agent name"`, `"agent$1"`, `create_runnable_app(prefix="/agents", runnables={key: stub})` raises `ValueError`. For keys `"a"` and `"a"*64`, the factory returns a `FastAPI` instance. Use a tiny stub class that has `async def ainvoke(...)` / `async def abatch(...)` (only needed for valid-key cases).
    - VC-106b (FR-105): `create_runnable_app(prefix="/agents", runnables=collections.UserDict({"a": stub}))` raises `TypeError`; same for `types.MappingProxyType({"a": stub})`.
    - VC-112 (FR-112): `import langgraph_runnable_server as m; assert set(m.__all__) == {"create_app", "create_runnable_app"}` (exact equality, no extras).
    - VC-114 (FR-108): each of the following raises `ValueError` with a message that includes the colliding path:
      - `create_runnable_app(prefix="/health", runnables={"x": stub}, create_app_prefix="/")` (collides with probe `/health`).
      - `create_runnable_app(prefix="/metrics", runnables={"x": stub}, create_app_prefix="/")` (collides with probe `/metrics`).
      - A constructed direct-equality case: `create_runnable_app(prefix="/", runnables={"health": stub_h, "metrics": stub_m}, create_app_prefix="/")` produces routes `/health/invoke` etc. — confirm whether spec considers this a collision; if FR-108 reads as "full path equality only", this case is NOT a collision (no exact match). Document the interpretation in test comments.
    - **Prefix normalization (FR-111)** parametrized: invalid prefix values (`"agents"` missing leading `/`, `"/agents//x"` containing `//`, `"/agents space"` non-pchar) → `ValueError`; valid forms (`"/"`, `"/agents"`, `"/agents/"` → normalized to `"/agents"`) → factory returns.
    - **`create_app_prefix` forwarding (FR-112)**: `create_runnable_app(prefix="/agents", runnables={}, create_app_prefix="/api")` produces an app where `GET /api/health` → 200 `b"ok"` and `GET /health` → 404 (proves probe prefix is `/api`).
    - **Empty `runnables` (FR-106, partial)**: `create_runnable_app(prefix="/agents", runnables={})` returns successfully; `GET /health` → 200; no `POST /agents/...` route exists yet (will be deepened in iter 2).
  - `test_namespace_validation.py`:
    - VC-121 validation slice (the metric-name expansion is iter 4; this iter only verifies the validation rejections):
      - `metrics_namespace=123` → `TypeError`.
      - `metrics_namespace="1bad"` → `ValueError`.
      - `metrics_namespace="bad-name"` → `ValueError`.
      - `metrics_namespace="bad:name"` → `ValueError`.
      - `metrics_namespace="bad name"` → `ValueError`.
      - `metrics_namespace=""` → accepted (factory returns); `app.state["metrics_namespace"] == ""`.
      - `metrics_namespace="acme_agents"` → accepted; `app.state["metrics_namespace"] == "acme_agents"`.
      - Default (omitted) → `app.state["metrics_namespace"] == "langgraph_runnable_server"`.
- Module-level docstring on `runnable_app.py` lists: the public signature, the validation rules, the path-collision interpretation choice (per the flag above), and an explicit "no runnable routes yet — iter 2" line that will be removed once routes are added.

**Out of scope** (deferred to later iterations):
- Any runnable route registration (`POST /agents/{key}/invoke`, `…/batch`).
- Request/response body parsing or `jsonable_encoder` wiring.
- Prometheus metric families (only `app.state["metrics_namespace"]` is stored in this iter; no registry yet).
- Structlog middleware.
- Method discipline (405 / 404) on runnable routes.

**Success criteria**:
- VC-106 (invalid key rejection + length boundary acceptance): all parametrized cases pass.
- VC-106b (non-`dict` `runnables` rejection): both `UserDict` and `MappingProxyType` cases raise `TypeError`.
- VC-112 (`__all__` discipline): exactly `{"create_app", "create_runnable_app"}`.
- VC-113 (partial — empty runnables factory does not crash): factory returns; `GET /health` 200.
- VC-114 (path collision rejected): each documented case raises `ValueError` naming the colliding path.
- VC-121 (validation slice only — name expansion deferred to iter 4): each invalid `metrics_namespace` value raises the documented exception; accepted values store the value at `app.state["metrics_namespace"]`.
- FR-111 normalization reuse: parametrized invalid/valid prefix cases match spec 01 FR-011 behavior.
- FR-112 forwarding: `create_app_prefix` honored for probes; `lifespan=None` default works (real lifespan test in iter 2).
- All tests run: `uv run pytest tests/ -q` → green. The full pre-existing spec 01 test suite must remain green (no regressions in `test_health_and_metrics.py`, `test_methods.py`, `test_acceptance.py`).
- Tooling: `uv run ty check` exit 0; `uv run ruff check .` and `uv run ruff format --check .` exit 0.
- Dependency lockfile: `uv sync --frozen` exit 0; `uv lock --check` exit 0; `langchain-core`, `structlog`, `prometheus-client` all present in `uv.lock` at the declared floor.

**Documentation updates**:
- `README.md`: add a top-level "Runnable HTTP surface" section with a stub example that links to `specs/02-runnable-support.md` and notes that v0.2 only ships factory validation; full surface comes in subsequent iterations (this hint is removed in iter 6).
- Module docstring in `src/langgraph_runnable_server/__init__.py`: mention both factories.
- Module docstring in `src/langgraph_runnable_server/runnable_app.py`: explain validation order and the FR-108 interpretation choice.
- `CHANGELOG.md`: add an unreleased "v0.2" entry covering iter 1 deliverables.

**Commit message** (draft):
- `feat(runnable): add create_runnable_app factory skeleton with argument validation`

---

## Iteration 2: Runnable routes — `invoke` and `batch` happy path, serialization, lifespan passthrough

**Goal**: Make `POST {runnables_base}/{key}/invoke` and `POST {runnables_base}/{key}/batch` route correctly to each runnable's `ainvoke` / `abatch`, serialize results with FastAPI's `jsonable_encoder`, honor the empty-batch short-circuit, and verify host-supplied lifespan flows through verbatim — **no** error handling, validation paths, metrics, or logging yet.

**Scope**:
- Inside `create_runnable_app`, after validation, register routes per `runnables` map.
- Choose a registration strategy: a single `APIRouter` populated in a loop with one `add_api_route` per `(key, "invoke")` and `(key, "batch")`, mounted at `{runnables_base}` (or at app level if `{runnables_base}` is empty, matching the spec 01 base-vs-empty convention). Routes Must use FastAPI **route templates** consistent with FR-130 / BR-301 — implementations may use literal paths `f"/{key}/invoke"` per key (preferred for v1, simpler than template parameters) **or** a templated `/{runnable_key}/invoke` path with a string-validating dependency that rejects unknown keys with 404. Pick the literal-path approach for simplicity and exact 404 semantics on unknown keys; document the chosen `http.route` value used by BR-301 (it will be the literal e.g. `/agents/agent1/invoke`, which is also a stable template per route — note that VC-109 / BR-301 say "registered route template" and the example shows `/agents/{key}/invoke`; if literal paths are used, the wide event's `http.route` will be the literal path, which IS the registered template). **Flag this discrepancy with VC-109 example wording** and resolve it in iter 5 logging; for iter 2, just record the chosen route shape so logging can pick it up.
- Handler logic for `invoke` (BR-101, BR-103):
  - Accept `Request` (FastAPI) and read JSON body via `await request.json()`. Wrap in `try` for `json.JSONDecodeError` and re-raise as `HTTPException(status_code=422, …)` (iter 3 will harden this — for iter 2, the happy path is the only assertion).
  - Validate body is a JSON object with key `"input"` (no validation in iter 2 beyond raising — iter 3 will codify error shape). For iter 2 the test corpus is always well-formed.
  - Extract `input = body["input"]` and `config = body.get("config")`.
  - Call `result = await runnable.ainvoke(input, config=config)` (if `config` is `None`, pass it through anyway — runnable Must accept `config=None`; per the `Runnable` contract `config` defaults are accepted).
  - Encode with `from fastapi.encoders import jsonable_encoder; payload = jsonable_encoder(result)` (BR-103: no `default=` fallback; no `langchain` `dumps`/`dumpd` wrapping).
  - Return `JSONResponse(payload, status_code=200)`.
- Handler logic for `batch` (BR-102, BR-103):
  - Read body, extract `inputs` (must be a JSON array per BR-102) and optional `config`.
  - **Empty-batch short-circuit (BR-102)**: if `inputs == []`, return `JSONResponse([], status_code=200)` **without** calling `runnable.abatch`. The test stub Must record zero `abatch` invocations for this case.
  - Otherwise: `result = await runnable.abatch(inputs, config=config)`; return `JSONResponse(jsonable_encoder(result), status_code=200)`.
- Route closures Must capture the specific runnable instance per key — beware Python late-binding closure pitfalls in loops; use `functools.partial` or factory functions to bind `runnable` and `key` explicitly. **Verify no cross-routing** (FR-104) with an explicit two-key test.
- Tests under `tests/interface/`:
  - `test_runnable_routes.py`:
    - **Test stub class** at module scope (single source of truth for stubs, reused by iter 3+):
      ```python
      class StubRunnable:
          def __init__(self, response=None, raise_on_invoke=None, raise_on_batch=None):
              self.ainvoke_calls: list[tuple[object, object]] = []
              self.abatch_calls: list[tuple[object, object]] = []
              self._response = response
              self._raise_on_invoke = raise_on_invoke
              self._raise_on_batch = raise_on_batch
          async def ainvoke(self, input, config=None):
              self.ainvoke_calls.append((input, config))
              if self._raise_on_invoke is not None:
                  raise self._raise_on_invoke
              return self._response if self._response is not None else {"echo": input}
          async def abatch(self, inputs, config=None):
              self.abatch_calls.append((inputs, config))
              if self._raise_on_batch is not None:
                  raise self._raise_on_batch
              return [{"echo": i} for i in inputs]
      ```
    - VC-101 (partial — runnable routing only, metrics deferred): two runnables under `prefix="/agents"`; `GET /health` 200; `POST /agents/a/invoke {"input": {"x": 1}}` → 200 body `{"echo": {"x": 1}}`; `r_a.ainvoke_calls == [({"x": 1}, None)]`; `r_b.ainvoke_calls == []`.
    - VC-102 (path layout): under `prefix="/agents"` with keys `agent1`, `agent2`, verify each of the four expected routes responds and that `POST /agents/agent1/stream` → 404.
    - VC-103 (ainvoke + abatch wiring + arg deserialization per BR-101 / BR-102):
      - `POST .../invoke {"input": {"x": 1}, "config": {"tags": ["t"]}}` → 200; stub's `ainvoke_calls[0] == ({"x": 1}, {"tags": ["t"]})`.
      - `POST .../batch {"inputs": [{"x": 1},{"x": 2}], "config": {"tags": ["t"]}}` → 200, body `[{"echo": {"x": 1}},{"echo": {"x": 2}}]`; stub's `abatch_calls[0] == ([{"x": 1},{"x": 2}], {"tags": ["t"]})`.
    - FR-104 (no cross-routing): two distinct keys, each call hits exactly its runnable.
    - **Empty inputs short-circuit (BR-102)**: `POST .../batch {"inputs": []}` → 200, body `[]`, `stub.abatch_calls == []`.
    - **Null input (BR-104 partial — null is valid)**: `POST .../invoke {"input": null}` → 200; `stub.ainvoke_calls[0] == (None, None)`.
    - **BR-103 jsonable_encoder coverage**: a stub returning a `pydantic.BaseModel` (small inline class with one field), a `datetime`, a `UUID`, a `set` (encodes as JSON array). Each round-trips through `/invoke` to a plain JSON body, no `{"lc": 1, …}` envelope present in any response. (Use `langchain_core` message types only if importable cheaply; otherwise inline Pydantic models suffice.)
    - **FR-106 (full)**: `create_runnable_app(prefix="/agents", runnables={})` — `GET /health` 200; `POST /agents/foo/invoke` → 404 (no route registered).
    - VC-111 (lifespan passthrough, FR-112): host supplies a lifespan that toggles `app.state.started = True` on startup and `app.state.shutdown = True` on shutdown; pass it via `lifespan=`; assert flags before/after `TestClient` context. The library Must Not wrap or compose this lifespan (verified by setting a sentinel marker inside the host lifespan and asserting it's the one that ran — mirror VC-003b from spec 01 plan).
- All existing spec 01 tests continue to pass (regression sanity).

**Out of scope** (deferred):
- All explicit validation error paths (malformed JSON, missing `"input"`, wrong root type, missing/wrong `Content-Type`) — iter 3.
- Method discipline (`GET .../invoke` → 405; FR-109 5xx envelope) — iter 3.
- Prometheus metric registration and `/metrics` body replacement — iter 4.
- Structlog wide events — iter 5.
- Cancellation propagation (BR-108) — iter 3 (it ties to error visibility).

**Success criteria**:
- VC-101 (partial): probe routes intact; runnable routing works; metric portion deferred.
- VC-102: four expected POST paths exist; out-of-scope paths return 404.
- VC-103: `ainvoke` / `abatch` receive deserialized arguments per BR-101 / BR-102 exactly.
- FR-104 (no cross-routing): two-key parametrized test passes.
- BR-102 empty-batch short-circuit: `_count == 0` on the stub's `abatch_calls`.
- BR-103 serialization: Pydantic, `datetime`, `UUID`, `set` all encode to plain JSON, no LangChain envelope.
- FR-106 (full): empty `runnables` produces a working probe-only app.
- VC-111: host lifespan startup and shutdown observable around `TestClient` context; library does not wrap.
- All tests green: `uv run pytest tests/ -q`. Pre-existing spec 01 tests remain green.
- Tooling green: `uv run ty check`, `uv run ruff check .`, `uv run ruff format --check .` all exit 0.

**Documentation updates**:
- `README.md`: replace iter 1's stub section with a working `create_runnable_app` example showing `prefix="/agents"`, one stub runnable, and a `curl` invoking `POST /agents/foo/invoke`. Document the `invoke` and `batch` request body shapes and the JSON encoder choice (BR-103).
- Module docstring on `runnable_app.py`: list the registered route patterns and the empty-batch short-circuit behavior.
- `CHANGELOG.md`: update v0.2 entry — "runnable routes (happy path)".

**Commit message** (draft):
- `feat(runnable): register invoke and batch routes with jsonable_encoder serialization`

---

## Iteration 3: Validation paths, error envelope, method discipline, cancellation

**Goal**: Implement every documented client-error and server-error path: malformed JSON, missing required keys, wrong root types, `Content-Type` enforcement, runnable exceptions → 500 with `{"detail": …}` envelope (no traceback to client), non-POST → 405, unknown key → 404, and cooperative cancellation on client disconnect.

**Scope**:
- Body-parsing pipeline for `invoke` and `batch`:
  - Pre-parse guard for `Content-Type` (BR-109): if the request has a non-empty body and `Content-Type` is missing or not `application/json` (case-insensitive on the media type), raise `HTTPException(status_code=422, detail="Content-Type must be application/json")`. Use FastAPI's `Request.headers` directly — do not rely on Pydantic body parsing for this check, since FastAPI's automatic 422 on missing `Content-Type` covers most cases but the spec wants a deterministic rule.
  - JSON parse with `try/except json.JSONDecodeError` → 422 with a structured detail.
  - Root-type check (BR-109): if `not isinstance(body, dict)` → 422. A JSON array, number, string, boolean, or `null` at the root is rejected.
  - Field check:
    - For `invoke`: require `"input"` present (any JSON value, including `null` per BR-104). If absent → 422.
    - For `batch`: require `"inputs"` present and `isinstance(inputs, list)`. If absent or wrong type → 422.
  - All 422 responses Must be JSON of the form `{"detail": "<message>"}` (FR-109 key alignment — applies to all error responses, not just 500).
- Runnable exception handling (FR-109):
  - Wrap `await runnable.ainvoke(...)` / `await runnable.abatch(...)` in `try/except` (catch `Exception`, **not** `BaseException` — `asyncio.CancelledError` is a `BaseException` in Py3.12 and Must propagate per BR-108).
  - On `Exception`: produce `JSONResponse({"detail": str(exc)}, status_code=500)`. Do **not** include traceback in the response. The traceback is captured for the structlog wide event in iter 5 — for iter 3, store the exception type and traceback on the request scope or a thread-local for iter 5 to read, OR pre-build the structured logger hook here so iter 5 only adds the field assertion. Choose the simpler path: re-raise inside a try/finally that records `request.state.exception` (or similar) so the iter-5 middleware can read it; the immediate handler still returns 500. (**Implementation flag**: ensure the 500 response is produced **after** the exception is recorded, so the iter-5 middleware sees the response status 500 AND the exception object.)
- Method discipline (BR-105): `add_api_route(..., methods=["POST"])` per route. FastAPI/Starlette's default 405 handler covers `GET /agents/agent1/invoke` etc. Verify no library-side override accidentally maps this to 404.
- Unknown key → 404: since routes are registered per literal key path, an unknown key like `/agents/no_such_key/invoke` naturally hits FastAPI's default 404 (no matching route). No additional code needed; just verify in tests.
- Cancellation (BR-108):
  - Use `try/finally` around the `await runnable.…` call. On `asyncio.CancelledError`: do **not** swallow — re-raise so the runnable is cancelled cooperatively.
  - Record an internal cancellation marker on `request.state` so iter 5's logging middleware can emit a wide event with a non-200 status. The HTTP response is moot (client gone), so no `JSONResponse` is constructed.
  - For iter 3 test coverage, simulate cancellation: spawn a task that issues the request, then `task.cancel()` mid-flight using `asyncio` directly (not `TestClient`, which doesn't support cancellation well — use `httpx.AsyncClient` with the FastAPI app via `httpx.ASGITransport`). Assert the stub's `ainvoke` received a `CancelledError` propagation (the stub Must observe the cancellation — implement the stub with an `await asyncio.sleep(...)` and verify it was cancelled). **If `httpx.ASGITransport` cancellation testing proves brittle**, mark this test as integration-level and provide a unit-level alternative that calls the handler function directly with a cancelled task.
- Tests under `tests/interface/`:
  - `test_runnable_validation.py` (VC-104, VC-119, BR-104, BR-109):
    - `POST .../invoke {}` → 422; response body has `detail`.
    - `POST .../invoke {"input": null}` → 200 (null is valid).
    - `POST .../invoke {"foo": 1}` → 422 (missing `input`).
    - `POST .../invoke [1, 2, 3]` → 422 (root is array).
    - `POST .../invoke 42` → 422 (root is number).
    - `POST .../invoke "hello"` → 422.
    - `POST .../invoke true` → 422.
    - `POST .../invoke null` → 422.
    - `POST .../invoke` raw bytes with no `Content-Type` header → 422.
    - `POST .../invoke {"input": 1}` with `Content-Type: text/plain` → 422.
    - Malformed JSON (`POST .../invoke 'not json'` with `Content-Type: application/json`) → 422.
    - For `batch`:
      - `POST .../batch {}` → 422 (missing `inputs`).
      - `POST .../batch {"inputs": "not a list"}` → 422.
      - `POST .../batch {"inputs": []}` → 200, body `[]` (already in iter 2, regress-check here).
  - `test_runnable_errors.py` (VC-105, VC-118, FR-109):
    - VC-105 / FR-109 (server error path): stub raises `RuntimeError("boom")`; `POST .../invoke` → 500; body is JSON with `{"detail": "boom"}` (or message containing `boom`); body MUST NOT contain the substrings `"Traceback"`, `"File \""`, or any internal module names.
    - VC-118 partial (client-side discipline only; structlog-side traceback assertion deferred to iter 5): assert 500 envelope shape and that no stack trace leaks.
  - `test_runnable_methods.py` (BR-105):
    - `GET /agents/agent1/invoke` → 405.
    - `PUT /agents/agent1/invoke` → 405.
    - `DELETE /agents/agent1/invoke` → 405.
    - `POST /agents/no_such_key/invoke` → 404.
    - `GET /agents/agent1/batch` → 405.
  - `test_runnable_cancellation.py` (BR-108):
    - Using `httpx.AsyncClient(transport=httpx.ASGITransport(app))` plus `asyncio.wait_for(..., timeout=0.1)` to force cancellation while stub `ainvoke` is sleeping. Assert the stub observed `asyncio.CancelledError`. If brittle in CI, gate behind a fixture and document the alternative direct-coroutine test (call the handler with a pre-cancelled task).

**Out of scope** (deferred):
- Prometheus metrics counting errors / requests / sizes / duration — iter 4.
- Structlog wide event including exception traceback — iter 5.

**Success criteria**:
- VC-104: 422 on validation failures; error body shape correct. Metric-increment portion deferred.
- VC-105 (partial — error response shape only; `errors_total` increment deferred to iter 4): 500 with `detail` key.
- VC-118 (client-side discipline): no `Traceback` / `File "` in response body; traceback recorded internally for iter 5.
- VC-119: every parametrized body-validation case returns 422.
- BR-105: every non-POST method on a registered runnable path returns 405; unknown key returns 404.
- BR-108: cancellation propagates to runnable; handler does not swallow.
- All earlier iteration tests remain green.
- Tooling green.

**Documentation updates**:
- `README.md`: add a "Error handling" subsection documenting the `{"detail": "..."}` envelope, the 422/500/405/404 cases, and the cancellation contract.
- Module docstring on `runnable_app.py`: list the documented error responses.
- `CHANGELOG.md`: update v0.2 entry — "validation, error envelope, method discipline, cancellation".

**Commit message** (draft):
- `feat(runnable): client/server error responses, method discipline, cancellation`

---

## Iteration 4: Prometheus metrics — per-app registry, namespaced families, `/metrics` body, BR-106 isolation

**Goal**: Add the per-app Prometheus registry, register the five metric families (requests_total, request_duration_seconds, errors_total, request_size_bytes, response_size_bytes), wire the metric increments via middleware on the runnable subtree, replace the empty `/metrics` body with Prometheus text exposition, and verify that scraping `/metrics` does not increment invoke/batch metrics.

**Scope**:
- Create `src/langgraph_runnable_server/metrics/families.py` (or extend `metrics/registry.py`) with a builder function:
  ```python
  def build_metrics(namespace: str, registry: CollectorRegistry) -> MetricFamilies:
      ...
  ```
  where `MetricFamilies` is a small `NamedTuple`/`dataclass` holding the five metric objects. Pass `namespace=namespace` and `registry=registry` to each `Counter` / `Histogram` constructor.
- Inside `create_runnable_app`:
  1. After validation, **before** calling `create_app`, build a per-app `CollectorRegistry()` (one instance per factory call).
  2. Call `metric_families = build_metrics(metrics_namespace, registry)` to register all five families on this dedicated registry. The metric base names: `requests_total`, `request_duration_seconds`, `errors_total`, `request_size_bytes`, `response_size_bytes`. If `metrics_namespace == ""` no prefix is added. Otherwise the names become `{metrics_namespace}_requests_total` etc. (`prometheus_client.Counter(name, ..., namespace=ns)` Must NOT be used when `ns == ""` because empty-namespace + name semantics differ — prefer to compose the full name manually as `f"{ns}_{base}" if ns else base` and pass it as the metric name with `namespace=""`/no `namespace=`, or pass `namespace=ns` only when non-empty. Pick the explicit composition approach for predictability.)
  3. Store `app.state["metrics_registry"] = registry` on the FastAPI app returned by `create_app(...)` (after the call). `app.state["metrics_namespace"]` was already set in iter 1.
- Replace `/metrics` body for `create_runnable_app` apps (FR-120, "Amendments to spec 01"):
  - The probe `/metrics` route in `api/routes/metrics.py` currently returns an empty body unconditionally for spec 01. For `create_runnable_app` apps, the route Must instead return `prometheus_client.generate_latest(registry)` with `Content-Type: text/plain; version=0.0.4; charset=utf-8` (= `prometheus_client.CONTENT_TYPE_LATEST`).
  - Implementation choice (pick the cleaner of the two):
    1. **Replace at composition time**: after `create_app(...)` returns, *remove* the spec-01 `/metrics` route from `app.router` and re-register a new `/metrics` route that generates exposition from `app.state["metrics_registry"]`. Risk: depending on FastAPI internals to remove a route.
    2. **Conditional route in `metrics.py`**: the router reads `request.app.state` for a `"metrics_registry"` key; if present, it generates Prometheus text; otherwise returns empty (spec 01 default). This keeps the route registration unified and uses runtime dispatch.
    Choose **option 2** — it's framework-friendly, preserves spec 01 behavior for `create_app`-only apps (BR-003 still holds), and avoids fragile route mutation.
  - Update `api/routes/metrics.py` accordingly:
    ```python
    @router.get("/metrics")
    def metrics(request: Request) -> Response:
        registry = getattr(request.app.state, "metrics_registry", None)
        if registry is None:
            return Response(content=b"", status_code=200)
        body = generate_latest(registry)
        return Response(content=body, media_type=CONTENT_TYPE_LATEST, status_code=200)
    ```
    Make sure existing spec 01 tests still see `b""` (they don't set `metrics_registry`) and `text/plain` content-type. The spec 01 plan's VC-005 asserts `Content-Type` starts with `text/plain` — `text/plain` (no charset) is fine; Prometheus `text/plain; version=0.0.4; charset=utf-8` still starts with `text/plain` so prefixed-app probe tests still pass.
- Metrics middleware on the runnable subtree:
  - Implement a middleware that:
    1. Computes `route_path` from the matched route (FastAPI exposes `request.scope["route"].path` after routing; if needed, use a dependency or capture the matched route in the handler instead).
    2. Determines whether the request hit a runnable route by checking the path prefix against `{runnables_base}` and whether the suffix is `/invoke` or `/batch`. **Better**: tag the runnable routes at registration time with a marker (e.g. set `endpoint=<wrapped handler>` and stash `runnable`/`endpoint` metadata on `request.state`); the middleware reads `request.state.runnable` and `request.state.endpoint` (set by the handler before any logic runs). Pick whichever is cleaner.
    3. Records `request_size_bytes` (BR-203):
       - Read body once via `await request.body()`. **Caution**: this consumes the stream — FastAPI handlers downstream Must use the same body. Wrap the request so the body is replayed (Starlette's standard trick is to set `request._body = body`). Alternatively, do the size measurement inside the handler (after `await request.json()` succeeds) using `len(await request.body())` taken before `await request.json()`. Pick the handler-side approach: simpler and only paid for routes that actually parse a body.
       - On parse failure (early 422 before body fully consumed), follow BR-203: use `Content-Length` if present and well-formed; if absent or chunked, **omit** the histogram observation. Same omit rule applies to the wide-event `request_size_bytes` field in iter 5.
    4. Records `response_size_bytes` from `len(response.body)` (FastAPI's `JSONResponse.body` is bytes).
    5. Measures `duration_seconds` from `time.perf_counter()` taken on middleware entry to the point just before the response goes out — wall time per BR-202.
    6. Increments `requests_total{runnable=<k>, endpoint=<invoke|batch>}` for every request that reached a runnable handler (this includes the 500/422/404/405 cases that still routed). For 422/500, also increment `errors_total{runnable=<k>, endpoint=<invoke|batch>, http_status_class=<"4xx"|"5xx">}` per BR-201.
    7. Uses the BR-202 bucket set exactly: `(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)` plus `+Inf`. Pass `buckets=(...)` to the `Histogram` constructor.
  - BR-106 isolation: the middleware Must NOT instrument requests whose path is `{probe_base}/health` or `{probe_base}/metrics`. Implement this by gating the middleware on `request.state.runnable` being set (only the runnable handlers set it). Probe requests never set this state and so never increment runnable metric families.
- Tests under `tests/interface/`:
  - `test_runnable_metrics.py` (VC-107, VC-108, VC-115, VC-116, VC-117, VC-121 name expansion, finishes VC-101 and VC-105):
    - VC-101 (full): scrape `/metrics` after one runnable POST returns 200 with Prometheus text and `Content-Type` `text/plain; version=0.0.4; charset=utf-8`.
    - VC-107: default namespace, one `invoke` and one `batch` on `agent1`, one `invoke` on `agent2`; scrape and assert exact counter values for the three expected label sets, no series for `agent2,batch`, no `errors_total` series.
    - VC-105 (full): runnable raises → 500, AND `errors_total{...,http_status_class="5xx"}` increments by 1, AND `requests_total{...}` still increments by 1, AND `request_duration_seconds_count` still increments by 1 (the duration is recorded even on error per FR-121).
    - VC-104 (full): 422 path → `errors_total{...,http_status_class="4xx"}` increments; `requests_total{...}` increments. Caveat: for early `Content-Type` rejection where the route Cannot be matched (no key resolved), document whether the error counter increments — the spec implies it Should for any 4xx response from a routed handler, but not for FastAPI's automatic 405/404 (those don't have a `runnable` label). For the VC-104 cases (which hit a registered route), the counter Must increment with both labels.
    - VC-108: known-size payloads; assert `_sum` equals the byte count of the request body and response body; assert `_count == 1`.
    - VC-115 (BR-106 scrape isolation): one POST → counter at 1; five consecutive scrapes of `/metrics`; counter still at 1 (scrapes did not increment any runnable family).
    - VC-116 (BR-202 bucket discipline): scrape `/metrics`; parse the `request_duration_seconds_bucket` series; assert the set of `le=` labels equals `{"0.005", "0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1.0", "2.5", "5.0", "10.0", "+Inf"}` exactly. Use a small Prometheus-text parser (e.g. `prometheus_client.parser.text_string_to_metric_families`).
    - VC-117 (FR-122 per-app registry isolation): two apps in one process, same prefix, same key; each scrape shows only its own app's increments; `app1.state["metrics_registry"] is not app2.state["metrics_registry"]`; neither uses `prometheus_client.REGISTRY` (assert `metric not in prometheus_client.REGISTRY._names_to_collectors`).
    - VC-121 (full name expansion): three apps with namespace `"langgraph_runnable_server"` (default), `"acme_agents"`, and `""`; each scrape shows the correct metric name; cross-namespace isolation (custom-namespace app has no `langgraph_runnable_server_*` series; bare app has neither prefix). Validation slice tests from iter 1 remain green.
    - **NFR-106 (scrape parseability)**: after a smoke POST, run `promtool check metrics` against the scraped body if `promtool` is on `$PATH`; otherwise fall back to a pytest assertion that every required metric family appears at least once in the parsed exposition (use `prometheus_client.parser.text_string_to_metric_families`). Skip the `promtool` branch with `pytest.skip` if the binary is absent — do not fail CI on its absence.
    - **NFR-105 (label cardinality)**: not a runtime assertion in this iteration; documented in the spec (label set is bounded by `len(runnables) * 2`).
- BR-203 chunked-encoding edge case (omit rule):
  - Test with `httpx.AsyncClient` issuing a request with explicit `Transfer-Encoding: chunked` and no `Content-Length` (or with the body crafted to fail parsing before full consumption). Assert that the `request_size_bytes` histogram has no observation for that request (i.e. `_count` does not increase). The wide-event behavior (BR-301: same omit rule) is verified in iter 5.

**Out of scope** (deferred):
- Structlog wide events, including `request_size_bytes` / `response_size_bytes` log fields and the BR-203 omit-rule mirroring — iter 5.
- `uvicorn.access` log suppression discipline — iter 5.

**Success criteria**:
- VC-101 (full): probes + runnable + metrics all green in one app.
- VC-107: exact counter values per (runnable, endpoint).
- VC-108: byte-size histograms reflect known payload sizes.
- VC-115: scraping does not pollute `requests_total`.
- VC-116: BR-202 bucket set is exact.
- VC-117: two-app registry isolation.
- VC-121 (full): name expansion correct for default / custom / bare namespace; validation rejections from iter 1 still pass.
- VC-105 (full): runnable exception → 500 + `errors_total{...,5xx}` + `requests_total` + `request_duration_seconds_count` all increment.
- VC-104 (full): 422 + `errors_total{...,4xx}` + `requests_total`.
- NFR-106: scrape body parses (promtool if available, else family-presence pytest fallback).
- BR-106: probe routes do not increment runnable families.
- BR-203: chunked / size-unknown requests omit the histogram observation.
- All earlier iteration tests remain green.
- Tooling green.

**Documentation updates**:
- `README.md`: add a "Metrics" section listing the five families, their labels, the `metrics_namespace` argument (default, custom, bare-string examples), the BR-202 bucket set, and the BR-106 isolation guarantee.
- Module docstring on `runnable_app.py` and `metrics/families.py`: list metric names and labels.
- `CHANGELOG.md`: update v0.2 entry — "Prometheus metrics".
- NFR-110 (documentation string): the README "Metrics" section + module docstrings cover "metric names, label names, minimum log fields"; the log-fields portion lands in iter 5.

**Commit message** (draft):
- `feat(runnable): per-app Prometheus registry, namespaced metric families, BR-202 buckets`

---

## Iteration 5: Structured logging — structlog wide events, uvicorn access-log discipline

**Goal**: Emit exactly one structlog wide event per HTTP request (probes and runnable routes), with the BR-301 minimum field set, including `request_size_bytes` / `response_size_bytes` mirroring the BR-203 omit rule; ensure no second access-log line is added by the library; capture exception tracebacks for the wide event when a runnable raises.

**Scope**:
- Add `src/langgraph_runnable_server/logging.py` (or `wide_event.py`):
  - Configure a module-level structlog logger via `structlog.get_logger("langgraph_runnable_server")` and document the field set in a docstring (BR-301).
  - **Do not** call `structlog.configure(...)` at import time — that's the host's prerogative. The library binds to whatever processor chain the host has installed (or to structlog's default chain). Document this in `NFR-108`-aligned README content.
- Add a logging middleware on `create_runnable_app`-produced apps:
  - Registered alongside the metrics middleware (or fold into the same middleware to avoid duplicate per-request bookkeeping — pick the unified approach: one middleware, two responsibilities, single source of timing).
  - For every request (including probes):
    1. Capture `start = time.perf_counter()`.
    2. Attempt to parse `traceparent` (W3C trace-context per BR-301): if `request.headers.get("traceparent")` matches the W3C regex (`^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$`, lowercase), extract the 32-hex `trace-id` component; otherwise `trace_id` is **omitted** (not `None`).
    3. After response, compute `duration_ms = (time.perf_counter() - start) * 1000.0` (float, per BR-301 explicit type).
    4. Build the event payload:
       - `http.method`: from `request.method`.
       - `http.route`: the registered route template. For runnable routes Must be the literal registered path (e.g. `/agents/agent1/invoke`) — this is the "registered route template" per the chosen route-registration strategy in iter 2. For probes use `/health` / `/metrics` (or `{base}/health` / `{base}/metrics`). For unmatched routes (404) use `request.url.path` (the literal); document this choice. **Flag if the BR-301 example `/agents/{key}/invoke` implies templated paths**: in our literal-path registration each key gets its own literal route, so `http.route` for a request to `/agents/agent1/invoke` is `/agents/agent1/invoke`. If the spec is interpreted strictly (templated `/agents/{key}/invoke`), refactor route registration to use `add_api_route("/agents/{runnable_key}/invoke", ...)` with a `runnable_key: str = Path(...)` parameter validated against the registered keys (404 on unknown). Implement the literal path for v1 and note the alternative in the module docstring; if a host needs templated routes for log aggregation, they can revisit.
       - `http.status_code`: from `response.status_code`.
       - `duration_ms`: float.
       - `instance_id`: from `app.state["instance_id"]`.
       - `runnable`: only for runnable routes (read from `request.state.runnable`). **Omit** (not `None`) for probes.
       - `endpoint`: only for runnable routes (`"invoke"` or `"batch"`). **Omit** for probes.
       - `request_size_bytes`: integer when known per BR-203; **omitted** otherwise (same rule as the histogram observation — share the helper).
       - `response_size_bytes`: integer, **always present** per BR-301 (use `len(response.body)`).
       - `trace_id`: hex string when `traceparent` parses; **omitted** otherwise.
       - For runnable-exception cases, additional fields per VC-118: include `error.type` (exception class name) and `error.stack` (formatted traceback). Pull from `request.state.exception` captured by iter 3. **Pin the field name** to `error.type` / `error.stack` to satisfy VC-118's "(e.g. `exc_info` or `error.stack`)" hint.
       - For cancellation (BR-108): if `request.state.cancelled` is set, emit the wide event with `http.status_code=499` and the standard fields; best-effort means catch exceptions during emission and swallow (logging Must Not raise into the cancelled-task cleanup path).
    5. Emit at INFO via `log.info("http_request", **fields)` (event name `"http_request"` documented).
  - The middleware Must emit **exactly one** event per request (NFR-107). Guard against double-emission if the library is misconfigured (e.g. multiple registrations) — use a `request.state._logged = True` sentinel.
- Uvicorn access-log discipline (FR-131, FR-132, NFR-108):
  - The library does not configure uvicorn's logger; documentation only. README Must state: "Configure uvicorn with `access_log=False`. The library emits one structlog wide event per request via its middleware."
  - The library Must Not install any stdlib `logging` access handler that would duplicate per-request lines (VC-110). Audit the codebase to confirm no `logging.basicConfig`, no `logger.addHandler` on `uvicorn.access` or `fastapi` loggers.
- Tests under `tests/interface/`:
  - `test_runnable_logging.py` (VC-109, VC-110, BR-301, NFR-107, finishes VC-118):
    - Use `structlog.testing.capture_logs()` (preferred) or a custom processor that appends events to a list; install once via fixture.
    - VC-109: issue 5 mixed requests (`GET /health`, `GET /metrics`, two POSTs to `…/invoke`, one POST to `…/batch` that returns 422 via malformed body); assert exactly 5 events captured. Each event has `http.method`, `http.route`, `http.status_code`, `duration_ms` (float), `instance_id`. Runnable events Must have `runnable` and `endpoint`; probe events Must NOT have those keys (key-absent, not `null`). All five events Must have `request_size_bytes` (well-formed bodies have known sizes; the `Content-Type` rejection case at body length 0 omits per BR-203 — check whether the validation suite uses such a request and skip the assertion only for that case). All five Must have `response_size_bytes` as an integer (always present per BR-301).
    - VC-109 omit-vs-present: explicit case — issue a request with `Transfer-Encoding: chunked` and no `Content-Length` (use `httpx.AsyncClient`); assert the wide event for that request has **no** `request_size_bytes` key (`key not in event`, not `event["request_size_bytes"] is None`).
    - VC-110: install a `caplog` capture for stdlib loggers `uvicorn.access` and `fastapi`. Issue 5 requests via `TestClient`. Assert the structlog capture has 5 events AND the stdlib `uvicorn.access` / `fastapi` caplog have **zero** access-formatted records added by the library (records produced by the test harness itself are filtered by logger-name exclude).
    - NFR-107: 100 sequential `GET /health` → exactly 100 captured events, no duplicates.
    - VC-118 (full): runnable raises; the wide event for that request includes `error.type == "RuntimeError"` and `error.stack` containing the formatted traceback (substring `"Traceback"` is in the field value); the response body still has no traceback. Verify both halves in one test.
    - **`trace_id` parsing** (BR-301): parametrized test issuing requests with:
      - No `traceparent` header → event has no `trace_id` key.
      - `traceparent: 00-0123456789abcdef0123456789abcdef-0123456789abcdef-01` (valid W3C) → event has `trace_id == "0123456789abcdef0123456789abcdef"`.
      - `traceparent: malformed` → event has no `trace_id` key.
      - `traceparent: 00-ABCDEF...` (uppercase, invalid per W3C lowercase rule) → event has no `trace_id` key.
    - **Cancellation logging** (BR-108): combine with iter 3's cancellation test — the captured event Must have `http.status_code == 499` for the cancelled request (best-effort; if the cancellation interrupts before middleware runs, the test marks this branch as best-effort and asserts the no-extra-event invariant only).
    - **`http.route` value**: probe events have `http.route == "/health"` / `"/metrics"` (or `{base}/health` if `create_app_prefix != "/"`); runnable events have `http.route == "/agents/<key>/<invoke|batch>"` (literal per the iter-2 registration choice). Document this in the test docstring referencing the BR-301-vs-VC-109 wording flag from iter 5 scope.

**Out of scope** (deferred):
- The end-to-end VC-120 acceptance test — iter 6.
- Final README polish and NFR-110 final pass — iter 6.

**Success criteria**:
- VC-109: exactly one event per request; minimum fields present per request type; probe events omit `runnable`/`endpoint`; runnable events include them; size fields follow BR-203 omit-vs-present.
- VC-110: structlog has the events; stdlib `uvicorn.access` / `fastapi` loggers have zero library-added records.
- VC-118 (full): client-facing 500 has no traceback; wide event has `error.type` and `error.stack`.
- NFR-107: 100 health requests → exactly 100 events.
- BR-301 trace_id parsing: parametrized W3C cases pass.
- BR-108: cancellation event emits at 499 best-effort.
- All earlier iteration tests remain green.
- Tooling green.

**Documentation updates**:
- `README.md`: add a "Logging" section listing the wide event name (`http_request`), the BR-301 field set, the omit-vs-null discipline, the trace_id source (W3C `traceparent`), and the uvicorn `access_log=False` deployment guidance. Reference NFR-108 / NFR-111.
- Module docstring on `logging.py` (or `wide_event.py`): list every field with its type and the omit conditions.
- `CHANGELOG.md`: update v0.2 entry — "structured logging + access-log discipline".

**Commit message** (draft):
- `feat(runnable): structlog wide event per request with BR-301 fields`

---

## Iteration 6: End-to-end acceptance test + final documentation pass

**Goal**: Land VC-120 — the single end-to-end test that exercises the full `create_runnable_app` public surface in one run — and finalize all documentation (README sections, module docstrings, NFR-110 minimum-field listings, NFR-111 security-boundary note). Bump version to `v0.2.0` and freeze the lockfile.

**Scope**:
- Add `tests/interface/test_runnable_acceptance.py` containing one pytest test `test_full_runnable_surface` covering the ten sub-assertions of VC-120:
  1. **Probes intact** (spec 01 contract): `GET /health` → 200 `b"ok"`; `GET /metrics` → 200 Prometheus text with the exact `Content-Type`.
  2. **Routing isolation** (FR-102, FR-104).
  3. **Batch works** (FR-103, BR-102).
  4. **Method discipline** (BR-105): `GET /agents/agent1/invoke` → 405; `POST /agents/no_such_key/invoke` → 404.
  5. **Validation error** (BR-104): `POST /agents/agent1/invoke {}` → 422.
  6. **Runnable exception** (FR-109): a temporary stub raising → 500 with `detail`, no `Traceback`.
  7. **Metrics composed correctly** (default `metrics_namespace`): exact `requests_total`, `errors_total`, `request_duration_seconds_count` values.
  8. **Per-app registry and namespace** (FR-122, FR-123): `app.state["metrics_registry"]` is a `CollectorRegistry` and not `prometheus_client.REGISTRY`; `app.state["metrics_namespace"] == "langgraph_runnable_server"`.
  9. **Structlog wide events** (FR-130, BR-301): one event per HTTP request, with BR-301 minimum fields; runnable events include `runnable` / `endpoint`; probe events do not.
  10. **`__all__` discipline** (FR-112): `set(__all__) == {"create_app", "create_runnable_app"}`.
- Single test function — overlap with per-VC tests is intentional per VC-120's design (it's the "surface composes correctly" smoke test).
- Documentation finalization:
  - `README.md` final pass:
    - "Runnable HTTP surface" section: complete `create_runnable_app` example with two runnables, `prefix="/agents"`, default probes. Curl examples for `invoke` and `batch`. Sub-sections: "Request bodies", "Response bodies", "Error envelope", "Metrics", "Logging", "Cancellation", "Security boundary (NFR-111)".
    - Remove the iter-1 "subsequent iterations" hint.
    - Cross-link to spec 02 and to spec 01 amendments.
  - Module docstrings: confirm every public symbol carries an accurate one-line docstring referencing the spec section that defines it.
  - **NFR-110 (documentation string)**: the README sections on Metrics, Logging, and Request bodies together list metric names, label names, and minimum log fields. Add a single consolidated "Reference: spec field summary" subsection or table in the README that satisfies the literal NFR-110 phrasing.
  - **NFR-111 (security boundary)**: confirm the README's "Security boundary" subsection explicitly mentions: hosts Must front the library with a reverse proxy enforcing body size + timeouts; JSON nesting limits not enforced; cooperative `asyncio` cancellation per BR-108.
- `pyproject.toml`: bump `version = "0.2.0"`. Refresh `[project].description` if it still references only spec 01.
- `CHANGELOG.md`: cut a single `## v0.2.0 — 2026-05-11` (or current date) entry summarizing all iterations 1–6 deliverables. Move iter-by-iter unreleased notes into this entry.
- Verify the final acceptance commands:
  ```sh
  uv sync --frozen
  uv run ruff check .
  uv run ruff format --check .
  uv run ty check
  uv run pytest tests/ -q
  uv run pytest tests/interface/test_runnable_acceptance.py::test_full_runnable_surface -q
  ```
  All six Must exit 0. The last command alone is the spec 02 single-test acceptance gate, analogous to spec 01's VC-021.
- NFR-103 timing: confirm `time uv run pytest tests/interface/ -q < 60s` (NFR-103). If close to the bound, mark slow cancellation tests with `@pytest.mark.slow` and exclude from default runs while keeping VC-120 in the default path.

**Out of scope** (deferred — already deferred by spec):
- OQ-001 (Unicode keys).
- OQ-002 (per-item batch configs).

**Success criteria**:
- VC-120 (`test_full_runnable_surface`): single test passes with all ten sub-assertions green.
- NFR-103: full interface suite under 60s on a typical dev machine.
- NFR-101 / NFR-102: `uv run ty check` and `uv run ruff check .` / `uv run ruff format --check .` all exit 0.
- NFR-110: README + module docstrings list metric names, label names, and BR-301 minimum log fields in one consolidated place.
- NFR-111: README security-boundary subsection present and accurate.
- All earlier iteration tests remain green.
- Final acceptance command stack exits 0 end-to-end.

**Documentation updates**:
- `README.md`: completed full surface documentation (per the scope above).
- `CHANGELOG.md`: v0.2.0 release entry.
- Module docstrings: final consistency pass.

**Commit message** (draft):
- `feat(runnable): end-to-end acceptance test (VC-120) and v0.2.0 docs`

---

## Final Verification

Cross-check every requirement from spec v1.2 against the iteration that implements it and how to verify:

| Requirement | VC(s)              | Iteration(s)  | Verification                                                                                                                                  |
|-------------|--------------------|---------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| FR-101      | VC-101             | Iter 2 (probes wiring), Iter 4 (metrics composition completes VC-101) | `pytest` asserts probe + runnable routes coexist; `/metrics` returns Prometheus text. |
| FR-102      | VC-101, VC-102, VC-103 | Iter 2     | `POST {runnables_base}/{k}/invoke` routes to `runnables[k].ainvoke` exactly once.                                                              |
| FR-103      | VC-102, VC-103     | Iter 2        | `POST {runnables_base}/{k}/batch` routes to `runnables[k].abatch` per BR-102.                                                                  |
| FR-104      | VC-101, VC-102     | Iter 2        | Two-key parametrized test confirms no cross-routing.                                                                                          |
| FR-105      | VC-106b            | Iter 1        | `TypeError` on `UserDict` / `MappingProxyType`.                                                                                                |
| FR-106      | VC-113             | Iter 1 (factory returns), Iter 2 (probes work, no runnable routes) | Empty `runnables` produces probe-only app.                                       |
| FR-107      | VC-106             | Iter 1        | Invalid keys (slash, empty, length 65, whitespace, special chars) → `ValueError`. Length boundaries 1 and 64 accepted.                          |
| FR-108      | VC-114             | Iter 1        | Path-collision check raises `ValueError` naming the colliding path.                                                                            |
| FR-109      | VC-105, VC-118     | Iter 3 (envelope), Iter 4 (errors_total), Iter 5 (traceback in log) | 500 with `{"detail": …}`, no traceback to client, traceback in structlog event.       |
| FR-110      | VC-112, VC-121     | Iter 1        | Signature keyword-only with the documented parameters and types.                                                                              |
| FR-111      | VC-114 (collision uses normalized paths) | Iter 1 | Runnable `prefix` reuses spec 01 FR-011 normalization.                                                                                        |
| FR-112      | VC-111, VC-112     | Iter 1 (`__all__`), Iter 2 (lifespan passthrough) | `__all__ == ["create_app", "create_runnable_app"]`; host lifespan runs unwrapped.                                |
| FR-120      | VC-101, VC-116     | Iter 4        | `/metrics` returns 200 + Prometheus text + exact `Content-Type` + parses via promtool or family-presence check.                                |
| FR-121      | VC-105, VC-107     | Iter 4        | `requests_total` and `request_duration_seconds_count` increment on success AND on error.                                                       |
| FR-122      | VC-117             | Iter 4        | Per-app `CollectorRegistry` at `app.state["metrics_registry"]`; not `prometheus_client.REGISTRY`; two apps isolated.                            |
| FR-123      | VC-121             | Iter 1 (validation), Iter 4 (name expansion) | Default / custom / bare namespace; validation rejections.                                                                          |
| FR-130      | VC-109             | Iter 5        | Exactly one structlog event per request with BR-301 fields.                                                                                    |
| FR-131      | VC-110             | Iter 5        | No library-added stdlib access handler duplicating per-request lines.                                                                          |
| FR-132      | VC-110             | Iter 5        | No FastAPI-owned access line duplicates the wide event when host uses `NFR-108` settings.                                                       |
| BR-101      | VC-103             | Iter 2        | `input` → first positional arg to `ainvoke`; optional `config` → second.                                                                       |
| BR-102      | VC-103, VC-119, VC-120 | Iter 2 (happy path + empty short-circuit), Iter 3 (validation 422) | `inputs` is a list; empty list → `[]` without calling `abatch`.                              |
| BR-103      | VC-103, VC-120     | Iter 2        | `jsonable_encoder` round-trips Pydantic, `datetime`, `UUID`, `set`; no LangChain envelope.                                                     |
| BR-104      | VC-104, VC-119     | Iter 3        | 422 on malformed JSON / missing keys / wrong root type / wrong `Content-Type`; `null` input accepted.                                          |
| BR-105      | VC-119, VC-120     | Iter 3        | Non-POST on runnable paths → 405; unknown key → 404.                                                                                           |
| BR-106      | VC-115             | Iter 4        | `/metrics` scrapes do not increment runnable families.                                                                                         |
| BR-107      | VC-106             | Iter 1        | Key regex enforced; length 0 and 65 rejected; length 1 and 64 accepted.                                                                        |
| BR-108      | (BR-108 test in test_runnable_cancellation.py; partial in VC-109) | Iter 3 (propagation), Iter 5 (log 499) | Cancellation propagates; log event emits with 499 best-effort.                |
| BR-109      | VC-119             | Iter 3        | `Content-Type: application/json` required; root Must be a JSON object.                                                                         |
| BR-201      | VC-104, VC-105     | Iter 4        | `errors_total{...,http_status_class="4xx"\|"5xx"}` increments on ≥400 responses.                                                              |
| BR-202      | VC-116             | Iter 4        | Histogram buckets exactly `(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, +Inf)`.                                              |
| BR-203      | VC-108, VC-109     | Iter 4 (histogram), Iter 5 (log field) | Request-size omitted on chunked / parse-fail before consume; response-size always present.                                         |
| BR-301      | VC-109, VC-118, VC-120 | Iter 5     | Minimum field set; omit-vs-`null` discipline; `trace_id` from W3C `traceparent`.                                                              |
| NFR-101     | VC-111 (typing)    | every iter    | `uv run ty check` exit 0.                                                                                                                      |
| NFR-102     | (lint clean)       | every iter    | `uv run ruff check .` and `uv run ruff format --check .` exit 0.                                                                              |
| NFR-103     | VC-120             | Iter 6        | `time uv run pytest tests/interface/ -q` < 60s.                                                                                                |
| NFR-104     | VC-121             | Iter 1        | `langchain-core`, `structlog`, `prometheus-client` added with explicit floors; `uv.lock` updated; `uv sync --frozen` exit 0.                    |
| NFR-105     | (cardinality bounded by design) | Iter 4 | Label cardinality bounded by `len(runnables) * 2`; documented.                                                                                  |
| NFR-106     | VC-116, VC-120     | Iter 4        | `promtool check metrics` (if present) or family-presence pytest fallback.                                                                      |
| NFR-107     | VC-110             | Iter 5        | 100 health requests → exactly 100 structlog events.                                                                                            |
| NFR-108     | VC-110             | Iter 5 (assert + doc), Iter 6 (final doc pass) | README documents `uvicorn access_log=False`; library does not add stdlib access handlers.                       |
| NFR-109     | (Python pin)       | inherited from spec 01 | `.python-version == "3.12"`, `requires-python = ">=3.12"`.                                                                                |
| NFR-110     | VC-120             | Iter 6        | README and module docstrings list metric names, label names, BR-301 minimum log fields in one place.                                            |
| NFR-111     | (doc requirement)  | Iter 6        | README "Security boundary" section present with reverse-proxy / nesting-limit / cancellation guidance.                                          |
| (acceptance)| VC-120             | Iter 6        | Single-function test covering FR-102/103/104/105/106/109/110/111/112/120/121/122/123/130 + BR-101/102/103/104/105/107/301 in one run.          |

**Final acceptance test**: from a clean clone on Python 3.12, run

```sh
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest tests/ -q
uv run pytest tests/interface/test_runnable_acceptance.py::test_full_runnable_surface -q
```

All six commands must exit 0. The last command alone (VC-120) is spec 02's single-test acceptance gate.
