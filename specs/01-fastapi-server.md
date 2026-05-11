# Specification: Generic FastAPI Server (Library)

| Field | Value |
|-------|-------|
| Version | 1.8 |
| Last Updated | 2026-05-11 |
| Status | Draft |

## Purpose

Define a minimal **Python library** (installable package) whose **only public entry point** is **`create_app(prefix: str = "/", lifespan: Lifespan[FastAPI] | None = None)`**, returning a `FastAPI` app so a **host service** can run probes under a configurable **base path** without copying boilerplate, while optionally supplying its own startup/shutdown logic via the standard FastAPI/Starlette `Lifespan` contract. Operators hit **`{base}/health`** and **`{base}/metrics`** where `base` follows the prefix rules below. The library stays free of product-specific business logic, type-safe, and easy to verify in isolation.

## Scope

### In

- **Installable library** layout under `src/langgraph_runnable_server/` (import name `langgraph_runnable_server`, distribution name **`langgraph-runnable-server`** in `pyproject.toml` `[project].name`), published or vendored via `pyproject.toml` (PEP 621), with **`py.typed`** (PEP 561) and **`__all__ = ["create_app"]`** (no other public re-exports).
- **Single public callable:** **`create_app(prefix: str = "/", lifespan: Lifespan[FastAPI] | None = None) -> FastAPI`**, registering health and metrics routes under the **effective base path** derived from `prefix` (see FR-011, BR-004, BR-005), and wiring either the host-supplied lifespan or a built-in no-op lifespan (see FR-003, FR-013).
- Unique **instance identifier** on each returned app, readable only via the documented **`app.state`** key (see FR-001); not part of the import surface beyond `create_app`.
- FastAPI `lifespan` context manager on every app produced by `create_app`: a host-supplied lifespan when passed, otherwise a no-op placeholder.
- HTTP contracts for **GET `{base}/health`** and **GET `{base}/metrics`** where `{base}` is **`""` when `prefix` is `"/"`** (URLs `/health` and `/metrics`), or the normalized non-root prefix (e.g. `/api` → `/api/health`).
- Empty metrics registry module (structure only; no Prometheus emission in this spec).
- Automated verification via **pytest**, including **interface** tests against the app returned by the public factory.
- Project tooling per team standards: **uv**, **ruff**, **ty** (or agreed fallback), dependencies declared in `pyproject.toml` with explicit floors for `fastapi` and `starlette`; exact resolved versions captured in **`uv.lock`** (committed) and enforced in CI via `uv sync --frozen` (NFR-004, NFR-005).
- A pinned single Python version (**3.12**) used by both CI and local dev, recorded in `.python-version`, with a `requires-python = ">=3.12"` floor in `pyproject.toml` for downstream hosts (NFR-004).

### Out

- A mandatory standalone **executable service** or CLI entrypoint shipped inside this library (the **final service** repository may add `uvicorn`, `Dockerfile`, etc.).
- Authentication, authorization, rate limiting, and user/session models in the library.
- Any domain routes, persistence, background workers, or message consumers beyond the lifespan placeholder.
- Real Prometheus exposition format, custom metric collectors, or scraping configuration (only empty `/metrics` body and empty registry as specified).
- Container images, Helm charts, CI/CD for the **host** (may live only in the consuming service).
- Structured logging fields, tracing, or correlation middleware (instance ID must exist for future use but logging format is not prescribed here).

## Actors

| Actor | Description | Permissions |
|-------|-------------|---------------|
| **Host application developer** | Authors the final service; adds the library dependency; calls **`create_app()`**, **`create_app(prefix="...")`**, or **`create_app(prefix="...", lifespan=my_lifespan)`** and exposes the returned app (or mounts it as their ASGI entry). | Chooses `prefix` so probes match `{base}/health` and `{base}/metrics`; optionally supplies a `Lifespan[FastAPI]` callable to run host-side startup/shutdown; owns middleware and any routes outside this library. |
| **Orchestrator / load balancer** | Sends HTTP GET for liveness and may scrape metrics later against the **running host** URL map. | May call **`GET {base}/health`** and **`GET {base}/metrics`** at the paths resulting from the host’s chosen `prefix`, without credentials (no auth in scope). |
| **Operator / developer** | Runs the host process locally or in CI; runs library tests in this repo. | Same HTTP access against deployed URL map; may read logs and environment. |
| **Future metrics system** | May consume `/metrics` when extended. | Not in scope for this version beyond receiving an empty 200 body. |

## Functional Requirements

Requirements use **MoSCoW**: Must / Should / Could / Won’t (for this release).

### Application bootstrap and identity

- **FR-001 (Must)** — Given `app = create_app(...)`, when the factory returns, then **`app.state`** contains a documented key (e.g. **`"instance_id"`**) whose value is a **non-empty string**, stable for the **lifetime of that `app` object**. Hosts must not rely on any other symbol for instance identity (no public module-level export). Internal implementation may use private module state, but **observable contract** is `app.state` only. *Rationale:* a per-app `state` key gives each `create_app()` invocation its own identity (multiple apps in one process, tests, multi-tenant patterns), avoids import-time side effects, and prevents a module-level singleton from being shared across distinct app objects in the same interpreter.
- **FR-002 (Must)** — Given two separate OS processes each running `create_app()`, when each has returned an app, then the two **`app.state["instance_id"]`** values (or documented key) are not guaranteed to collide in practice (UUID v4 or equivalent).

### Lifespan

- **FR-003 (Must)** — Given an application object returned by `create_app`, when it is created, then it is **always** configured with an asynchronous context-manager `lifespan` that yields exactly once between startup and shutdown phases. The lifespan source is determined as follows:
  - If the caller passes `lifespan=None` (default), the library wires a built-in **no-op** lifespan whose startup and shutdown bodies do nothing.
  - If the caller passes a non-`None` `lifespan` argument (FR-010, FR-013), the library wires **that** callable verbatim as the FastAPI `lifespan=` parameter. The library does **not** compose, wrap, or chain it with any internal lifespan; the host-supplied callable wholly replaces the default.
  - In both cases, the resulting `FastAPI` instance has a registered lifespan (observable as `app.router.lifespan_context is not None`).
- **FR-013 (Must)** — Given the optional `lifespan` argument of `create_app` (FR-010), when it is not `None`, then it must conform to the FastAPI/Starlette `Lifespan[FastAPI]` contract: a callable that takes the `FastAPI` app and returns an `AsyncContextManager` (typically an `@asynccontextmanager`-decorated async generator that yields exactly once). The library accepts whatever FastAPI accepts at the `lifespan=` parameter; no additional validation is performed beyond static typing. *Rationale:* keep the surface aligned with FastAPI so hosts can move existing lifespans into the library without changes.

### Endpoints

Let **`{base}`** be the path prefix computed from the `prefix` argument per **FR-011** and **BR-005** (for default `prefix="/"`, endpoints are **`/health`** and **`/metrics`**).

- **FR-004 (Must)** — Given `app = create_app(prefix=...)` and a `TestClient` (or ASGI client) with **`app` mounted at the ASGI root** (no extra host-level URL prefix), when a client sends **`GET {base}/health`**, then the response has status **200**, `Content-Type` consistent with **plain text** (`text/plain`; see **A-006** for charset on the media type), and the response body is exactly the ASCII bytes for **`ok`** with **no** leading or trailing octets (**no** newline after `ok`; **BR-001**). The handler must not depend on external services (BR-002).
- **FR-005 (Must)** — Given the same `app` and client setup as FR-004, when a client sends **`GET {base}/metrics`**, then the response has status **200** and an **empty** body (zero-length body).
- **FR-014 (Must)** — Given the same `app` and client setup as FR-004, when a client sends any HTTP request to **`{base}/health`** or **`{base}/metrics`** whose method is **not** **`GET`**, then the response status is **404** (not **405**). *Rationale:* deterministic behavior for probes and scanners; the library does not advertise alternate methods on these paths.

### Package layout and discoverability

- **FR-006 (Must)** — Given the repository root, when a developer inspects `src/langgraph_runnable_server/`, then they find a private module that defines **`create_app`**, lifespan, and router wiring (implementation-defined), `api/` with routers (e.g. `routes/health.py`, `routes/metrics.py`), `metrics/registry.py` defining an **empty** metrics registry structure (not public API), **`py.typed`**, and **`__init__.py`** with **`__all__ = ["create_app"]`** re-exporting **`create_app`** only.
- **FR-007 (Should)** — Given the library, when tests or host code import the app, then they use the import name **`langgraph_runnable_server`**, which matches `pyproject.toml` `[project].name = "langgraph-runnable-server"` under the standard PEP 503 normalization (hyphens ↔ underscores). No duplicate app definitions in unrelated roots.

### Public API (library)

- **FR-009 (Must)** — The **only** names in **`__all__`** are **`"create_app"`**. Consumers obtain the app exclusively via **`from langgraph_runnable_server import create_app`** (or **`import langgraph_runnable_server as m; m.create_app`**). No other imports are supported as stable public API.
- **FR-010 (Must)** — **`create_app(prefix: str = "/", lifespan: Lifespan[FastAPI] | None = None) -> FastAPI`**:
  - `prefix` is optional; default **`"/"`** means **no path prefix** (routes at `/health` and `/metrics`). **`prefix=""`** is accepted and treated as **`"/"`** after **FR-011** normalization. Other non-root values set the base path for both routes per **FR-011**.
  - `lifespan` is optional and **keyword-recommended**; default **`None`** means the library installs its own no-op lifespan (FR-003). When non-`None`, it must be a `Lifespan[FastAPI]`-shaped callable (FR-013), and the library passes it through to `FastAPI(lifespan=...)` unchanged. `Lifespan` is the standard alias re-exported from `starlette.types` (and accepted directly by `fastapi`); the library does **not** re-export it (`__all__` stays `["create_app"]`, FR-009).
- **FR-011 (Must)** — **Prefix semantics:** `prefix` is a string. Normalization and validation run **before** any `FastAPI` instance is constructed, in order:
  1. **Trim** leading and trailing **ASCII whitespace** on `prefix`.
  2. If the trimmed string contains the substring **`//`** anywhere, **`create_app` raises `ValueError`** (reject empty path segments and malformed absolute paths; do not “fix” `//` into `/`).
  3. If the trimmed string is **empty** (e.g. caller passed **`""`** or only whitespace), treat it as **`"/"`** for the remaining steps.
  4. If the string does **not** start with **`/`**, **`create_app` raises `ValueError`**.
  5. Apply **BR-005** trailing-slash stripping so the working value is either **`"/"`** or a non-empty absolute path **without** a trailing **`/`** except the root literal **`"/"`**.
  6. If the result of step 5 is **`"/"`**, then **`{base}`** is empty and routes are **`/health`** and **`/metrics`**. Otherwise **`{base}`** is that string and routes are **`{base}/health`** and **`{base}/metrics`** with no doubled slash.
  7. For a **non-root** result (not **`"/"`** after step 5), the string must be a **valid URL path** by this **minimum** rule: it must **not** contain ASCII whitespace, **`?`**, or **`#`**; every character must be either **`/`** or an RFC 3986 **`pchar`** (i.e. `unreserved`, `sub-delims`, **`:`**, **`@`**, or **`pct-encoded`**). If the check fails, **`create_app` raises `ValueError`**.
- **FR-012 (Should)** — Given downstream type checkers, when they analyze `from langgraph_runnable_server import create_app`, then `py.typed` is present so the **`create_app`** signature and return type are visible.

### Testing

- **FR-008 (Must)** — Given the test suite, when interface tests run via `TestClient(create_app(...))` (or equivalent), then:
  - FR-004 and FR-005 hold for **`create_app()`** (default prefix), for **`create_app(prefix="")`** (same URL map as default per **FR-011** / **VC-017**), **and** for **at least one** additional accepted `prefix` (e.g. `"/api"`), using the resolved `{base}/health` and `{base}/metrics` paths, without live network binding unless the project standard requires it; and
  - **FR-014** is exercised by **VC-022** (non-**GET** methods on both probe paths for default and prefixed apps); and
  - the lifespan behavior in FR-003 is exercised by VC-003, including at least one test that passes a host-supplied `lifespan` to `create_app(...)` and asserts both startup and shutdown bodies run via the `TestClient` context-manager protocol.

## Business Rules

- **BR-001** — **Health body literal.** The liveness response body must be exactly the three ASCII octets for **`ok`** (lowercase), with **no** leading or trailing bytes—**no** newline or other suffix after `ok`. *Rationale:* probes and scripts often string-match; case sensitivity and exact length avoid ambiguity. *Exceptions:* none in this release.
- **BR-002** — **Health independence.** **`GET {base}/health`** must not perform I/O that depends on databases, caches, or third-party APIs. *Rationale:* liveness must reflect the process, not downstream outages. *Exceptions:* none.
- **BR-003** — **Metrics placeholder.** Until extended, **`{base}/metrics`** returns 200 with empty body; the registry module remains a structural stub (internal). *Rationale:* stable relative path for future Prometheus text. *Exceptions:* documented extension in a future spec only.
- **BR-004** — **Base path from `create_app(prefix=...)`.** All library HTTP routes hang under **`{base}`** derived from `prefix`. Default **`prefix="/"`**, **`prefix=""`**, or **whitespace-only** `prefix` (after **FR-011** trim) means **`{base}`** is empty: **`/health`** and **`/metrics`**. The host passes `prefix` to match their URL layout; they must not expect different paths without changing `prefix`. *Rationale:* one explicit knob instead of undocumented mounting. *Exceptions:* none.
- **BR-005** — **Prefix normalization.** After **FR-011** steps 1–4, implementations **strip trailing slashes** from the working string until it is **`"/"`** or no longer ends with **`/`**. **`"/"`** after full normalization means no leading segment on route names. For non-root prefixes, the effective base **starts with `/`** and **does not end with `/`**. *Rationale:* predictable URLs and no `//` in paths. *Exceptions:* invalid inputs (including any `//` substring) per **FR-011**; **`//` is never collapsed to root**.
- **BR-006** — **Probe methods.** Only **`GET`** is defined on **`{base}/health`** and **`{base}/metrics`**; any other HTTP method on those exact paths must yield **404** (**FR-014**), not **405**.

## Non-Functional Requirements

- **NFR-001** — **Type safety:** All Python modules under `src/langgraph_runnable_server/` that implement this spec are fully type-annotated for **`create_app`** and internal modules as needed; `ty check` (or project-agreed equivalent) passes with **zero** errors on CI. The package ships **`py.typed`** at `src/langgraph_runnable_server/py.typed`.
- **NFR-002** — **Lint/format:** `ruff check` and `ruff format --check` pass on CI for the same tree.
- **NFR-003** — **Tests:** Interface tests for FR-004 and FR-005 complete in **under 30 seconds** total on a typical developer machine (single process, no integration markers).
- **NFR-004** — **Python version:** A **single Python version is pinned for development and CI**: `.python-version` contains exactly `3.12` (no looser specifier). `pyproject.toml` declares `requires-python = ">=3.12"` so downstream hosts on Python 3.12 or later may install the wheel. Local and CI both use **uv** to create and use the environment (`uv sync --frozen` in CI; `uv sync` / `uv run` locally). Bumping the dev/CI Python version requires a spec changelog entry and a coordinated update of `.python-version`, `requires-python`, and `uv.lock`.
- **NFR-005** — **Dependency pinning and lockfile:** `pyproject.toml` declares **explicit lower bounds** for runtime dependencies (`fastapi`, `starlette`) and test dependencies (`pytest`, `httpx`). The floors are set to the **latest stable releases at the time of first implementation** (e.g., `fastapi>=X.Y.Z`, `starlette>=X.Y.Z`, recorded by the implementer when scaffolding the project) — the spec does not name version numbers because the spec does not gate releases; **the authoritative source of resolved versions is `uv.lock`**, which **must** be committed to the repository. CI executes `uv sync --frozen` so the lockfile is enforced (no opportunistic resolution). When a floor is raised, the implementer updates both `pyproject.toml` and `uv.lock` in the same change and notes it in the spec changelog only if a public-API or NFR-impacting reason exists. *Rationale:* the reviewer requested either named version pins or a single-Python + latest-stable policy; this NFR codifies the latter, with the lockfile providing the reproducibility guarantee the spec needs.

## Data & Interfaces

### HTTP

Paths are relative to **`{base}`** from **`create_app(prefix=...)`** (FR-011, BR-004, BR-005).

| Method | Path (default `prefix="/"`) | Path (example `prefix="/api"`) | Status | Content-Type | Body |
|--------|-----------------------------|----------------------------------|--------|----------------|------|
| GET | `/health` | `/api/health` | 200 | `text/plain` | `ok` |
| GET | `/metrics` | `/api/metrics` | 200 | (unspecified; empty body) | empty |

Any **non-GET** request to **`{base}/health`** or **`{base}/metrics`** must return status **404** (**FR-014**, **BR-006**, **VC-022**).

No request bodies, query parameters, or headers are required for conformance in this release.

### Module / layout (normative target — library)

```
<repo>/
├── pyproject.toml              # [project].name = "langgraph-runnable-server"; requires-python = ">=3.12"; floored deps (fastapi, starlette, ...)
├── uv.lock                     # committed; CI uses `uv sync --frozen` (NFR-005)
├── .python-version             # contains exactly "3.12" (NFR-004)
├── src/
│   └── langgraph_runnable_server/
│       ├── __init__.py         # public exports; __all__ = ["create_app"]
│       ├── py.typed
│       ├── <app_module>.py     # create_app(), lifespan, router registration (module name implementation-defined)
│       ├── api/
│       │   ├── __init__.py
│       │   └── routes/
│       │       ├── __init__.py
│       │       ├── health.py   # liveness handler (mounted under {base})
│       │       └── metrics.py  # metrics handler (mounted under {base})
│       └── metrics/
│           ├── __init__.py
│           └── registry.py     # empty registry structure
└── tests/
    └── interface/
        └── test_health_and_metrics.py   # or equivalent split files
```

Distribution name (PEP 621 `[project].name`): **`langgraph-runnable-server`**. Import name (Python package): **`langgraph_runnable_server`** (snake_case form per PEP 503 normalization). Both names must remain aligned with the wheel/sdist package layout.

**Host service (illustrative only, not part of this repo’s deliverable):**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from langgraph_runnable_server import create_app

app = create_app()  # /health, /metrics, no-op lifespan
# or:
app = create_app(prefix="/api")  # /api/health, /api/metrics, no-op lifespan

# or, with a host-owned lifespan for startup/shutdown work:
@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: open connection pools, warm caches, etc.
    yield
    # shutdown: drain queues, close pools, etc.

app = create_app(prefix="/api", lifespan=lifespan)
# ASGI: uvicorn <host_module>:app  (owned by the host project)
```

### Instance identity

- **Key / access:** **`app.state["instance_id"]`** (exact key name fixed in implementation docs; this spec uses **`instance_id`** as the normative example).
- **Semantics:** Stable for the lifetime of that **`app`** object; suitable for logging/metrics correlation in the host process. Not importable as a public symbol—only **`create_app`** is public (FR-009).

## Verification Criteria

### FR-001 / FR-002 — Instance ID

**VC-001: Instance ID in `app.state`**

- **Scenario:** Host obtains an app and issues at least one request.
- **Input/State:** `app = create_app()`; key documented as **`instance_id`** (or fixed alternate name in implementation docs).
- **Expected Result:** `app.state["instance_id"]` is a non-empty string; reading it twice for the **same** `app` yields the same value; format consistent with UUID v4 **or** documented equivalent.
- **Failure Mode Covered:** Missing key, empty string, regeneration on each request, reliance on a public module-level `INSTANCE_ID` export.

**VC-002: Distinct IDs for two apps in one process**

- **Scenario:** Two independent apps from the factory in the same interpreter.
- **Input/State:** `a = create_app(); b = create_app()`.
- **Expected Result:** `a.state["instance_id"] != b.state["instance_id"]` (negligible collision probability; assert inequality).
- **Failure Mode Covered:** Accidental shared singleton ID across app objects.

### FR-003 / FR-013 — Lifespan

**VC-003: Lifespan wiring (default and host-supplied)**

The criterion has two parts; **both** must hold.

- **VC-003a — Default lifespan is registered:**
  - **Scenario:** Caller passes no `lifespan` argument.
  - **Input/State:** `app = create_app()`.
  - **Expected Result:** `app.router.lifespan_context is not None` (a lifespan has been registered on the FastAPI app); opening `TestClient(app)` as a context manager completes the startup and shutdown phases without raising.
  - **Failure Mode Covered:** Library forgets to wire `lifespan=` on the FastAPI constructor, leaving only Starlette's default no-lifespan behavior.

- **VC-003b — Host-supplied lifespan runs startup *and* shutdown:**
  - **Scenario:** Caller supplies an `@asynccontextmanager`-decorated async generator.
  - **Input/State:** A test-owned lifespan such as:

    ```python
    from contextlib import asynccontextmanager
    from fastapi import FastAPI

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state["startup_ran"] = True
        yield
        app.state["shutdown_ran"] = True

    app = create_app(lifespan=lifespan)
    ```

    Then:

    ```python
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.get("/health")
        assert app.state["startup_ran"] is True
        assert "shutdown_ran" not in app.state  # not yet
    assert app.state["shutdown_ran"] is True  # set on context exit
    ```

  - **Expected Result:** `app.state["startup_ran"] is True` is observable inside the `with TestClient(app)` block; `app.state["shutdown_ran"] is True` is observable after the block exits.
  - **Failure Mode Covered:** Host-supplied lifespan silently ignored; only startup runs (no shutdown); library wraps/replaces the host lifespan with its own; library raises on a valid `Lifespan[FastAPI]` callable.

### FR-004 — Health

**VC-004: Health liveness contract (default and prefixed)**

- **Scenario:** Client requests liveness for default and non-default base paths.
- **Input/State:** `TestClient(create_app())` → `GET /health`; `TestClient(create_app(prefix="/api"))` → `GET /api/health` (or another validated non-root prefix per tests).
- **Expected Result:** Status 200; raw body equals ASCII **`ok`** with length **3** and **no** trailing newline (**BR-001**); `Content-Type` is **`text/plain`** or **`text/plain; charset=utf-8`** (**A-006**).
- **Failure Mode Covered:** Wrong path after prefix, wrong status, JSON body, wrong text, dependency on external failure.

### FR-005 — Metrics placeholder

**VC-005: Metrics empty 200 (default and prefixed)**

- **Scenario:** Client requests metrics for default and non-default base paths.
- **Input/State:** `TestClient(create_app())` → `GET /metrics`; `TestClient(create_app(prefix="/api"))` → `GET /api/metrics`.
- **Expected Result:** Status 200; raw body length 0.
- **Failure Mode Covered:** 404, non-empty body, 5xx, wrong prefix resolution.

### FR-014 — Non-GET on probe paths

**VC-022: Non-GET returns 404**

- **Scenario:** Clients use methods other than **GET** on the library probe URLs.
- **Input/State:** `TestClient(create_app())` and `TestClient(create_app(prefix="/api"))`; for each app, issue at least two distinct non-**GET** methods (e.g. **`HEAD`** and **`POST`**) against **`{base}/health`** and **`{base}/metrics`** (default: **`/health`**, **`/metrics`**; prefixed: **`/api/health`**, **`/api/metrics`**).
- **Expected Result:** Every such request has status **404**.
- **Failure Mode Covered:** **405 Method Not Allowed**, **200**, or other success on wrong method; inconsistent behavior between **health** and **metrics**.

### FR-006 / FR-007 — Layout

**VC-006: Library package layout**

- **Scenario:** Repository structure review (automated or manual checklist in CI optional).
- **Input/State:** File tree under `src/langgraph_runnable_server/`.
- **Expected Result:** Paths exist for the documented app factory module, `api/routes/health.py`, `api/routes/metrics.py`, `metrics/registry.py`, `py.typed`, and `__init__.py` with **`__all__ == ["create_app"]`** exactly. Import name **`langgraph_runnable_server`** matches the installable distribution **`langgraph-runnable-server`** declared in `pyproject.toml` (FR-007).
- **Failure Mode Covered:** Missing files, missing `py.typed`, routes defined only inline without separation (contradicts FR-006); drift between folder name and distribution metadata.

### FR-008 — Interface tests

**VC-007: Interface tests pass**

- **Scenario:** CI runs pytest for interface layer.
- **Input/State:** `uv run pytest tests/interface/ ...` (exact path per repo); tests cover **`create_app()`** and **`create_app(prefix=...)`** per FR-008.
- **Expected Result:** All tests green; FR-004 and FR-005 asserted for both path layouts.
- **Failure Mode Covered:** Regressions in status codes, body, routing, or prefix handling.

### FR-009 / FR-010 / FR-011 — Public API and prefix

**VC-015: Only `create_app` is public**

- **Scenario:** Stable import surface for hosts.
- **Input/State:** `from langgraph_runnable_server import create_app`; verify `__all__ == ["create_app"]` via `import langgraph_runnable_server as p; assert p.__all__ == ["create_app"]` in tests or equivalent.
- **Expected Result:** Import succeeds; **`create_app()`** and **`create_app(prefix="/api")`** each return a **`FastAPI`** instance.
- **Failure Mode Covered:** Extra public names, missing `create_app`, wrong return type.

**VC-016: py.typed present (FR-012)**

- **Scenario:** Wheel/sdist or source tree inspection.
- **Input/State:** Built artifact or `src/langgraph_runnable_server/py.typed`.
- **Expected Result:** File exists (may be empty).
- **Failure Mode Covered:** Omission breaks downstream type checking expectations for **`create_app`**.

**VC-018: Trailing slash on prefix normalized (BR-005)**

- **Scenario:** Host passes a prefix with a trailing slash.
- **Input/State:** `create_app(prefix="/api/")` (or equivalent accepted form).
- **Expected Result:** Same effective routes as `create_app(prefix="/api")` — i.e. **`GET /api/health`** succeeds, not **`GET /api//health`** as the only path.
- **Failure Mode Covered:** Double slash paths, 404 on valid probe URL.

**VC-019: Invalid prefix rejected (FR-011)**

- **Scenario:** Host passes a malformed prefix; valid empty / whitespace-only inputs are **not** in this VC (see **VC-017**).
- **Input/State:** At least: **`create_app(prefix="api")`** (no leading `/` after trim); **`create_app(prefix="//")`** and **`create_app(prefix="/api//v1")`** (contains **`//`**); a path with **ASCII whitespace**, **`?`**, or **`#`** inside the segment (e.g. **`"/a b"`**, **`"/a?b"`**); and a path with at least one character outside RFC 3986 **`pchar`** for segments (e.g. **`"/a<b"`**). Pick concrete examples in tests.
- **Expected Result:** Each case raises **`ValueError`** before any `FastAPI` instance is returned.
- **Failure Mode Covered:** Silent acceptance leading to wrong URLs, doubled slashes, or non-path characters in `{base}`.

### BR-001 — Literal `ok`

**VC-008: Exact body**

- **Scenario:** Health endpoint byte or string equality for resolved `{base}/health`.
- **Input/State:** At minimum `GET /health` on `create_app()`; optionally repeat for a prefixed app.
- **Expected Result:** Raw body **exactly** three octets **`ok`** per **BR-001** (no **`ok\n`** or **`ok\r\n`**); `Content-Type` per **A-006** where asserted.
- **Failure Mode Covered:** `OK`, `ok\n`, JSON-wrapped text, any suffix after `ok`.

### BR-002 — No external I/O in health

**VC-009: Health handler does not invoke network primitives (automated)**

- **Scenario:** The `GET {base}/health` handler must not perform external I/O.
- **Input/State:** A pytest test that, **before** issuing the request, patches the following network primitives with replacements that raise `AssertionError("health must not perform external I/O: <call>")` if invoked:
  - `socket.socket` (constructor) — covers stdlib TCP/UDP/Unix sockets.
  - `socket.socket.connect`, `socket.socket.connect_ex` — covers connection attempts on pre-constructed sockets.
  - `httpx.Client.send` and `httpx.AsyncClient.send` — covers `httpx`-based HTTP calls (the standard FastAPI ecosystem client).
  - `urllib.request.urlopen` — covers stdlib HTTP.

  With the patches active, the test issues `GET /health` against `TestClient(create_app())` and against `TestClient(create_app(prefix="/api"))` (i.e. `GET /api/health`).
- **Expected Result:** Both requests return **200** with body `ok` (BR-001) and **none** of the patched primitives raise — i.e. the health handler never touched any of them. If any patched primitive is invoked, the test fails with the descriptive `AssertionError`.
- **Failure Mode Covered:** Accidental DB ping, HTTP probe, or DNS resolution inside the health handler; silent regression where a future change adds a downstream call to the health path.

*Notes:*
- The list of patched primitives is the **minimum** set; the implementer may extend it (e.g. `psycopg`, `redis-py`, internal SDK clients) when adding new dependencies. Adding a runtime dependency that performs network I/O at import time must also be considered — if such a dependency is added, its module-level network entry point should be patched in this VC.
- The library's health handler is expected to be a pure function returning `"ok"`; this VC enforces that contract from the outside without inspecting source code.

### BR-003 — Metrics stub

**VC-010: Internal registry is empty (implementation check)**

- **Scenario:** Metrics remain a stub; optional source-level assertion in this repo’s tests.
- **Input/State:** Repository import of **`langgraph_runnable_server.metrics.registry`** in **tests only** (not a public API—FR-009), **or** reliance on VC-005 empty body only.
- **Expected Result:** No registered metrics **or** documented empty structure; **`GET {base}/metrics`** body remains empty per VC-005.
- **Failure Mode Covered:** Import error in package layout, accidental side effects that change `/metrics` body.

### BR-004 / BR-005 — Base path from `prefix`

**VC-017: `prefix` controls `{base}`**

- **Scenario:** Default vs explicit prefix produce the expected URL map on the **same** returned app mounting pattern (ASGI root); empty string prefix matches root.
- **Input/State:** `TestClient(create_app())`, `TestClient(create_app(prefix=""))`, and `TestClient(create_app(prefix="/api"))`.
- **Expected Result:** Default and **`prefix=""`** (and all-whitespace-only `prefix` if covered in tests): **`/health`**, **`/metrics`**. Prefixed: **`/api/health`**, **`/api/metrics`** (for that example).
- **Failure Mode Covered:** Routes ignoring `prefix`, wrong concatenation, host-level confusion without `prefix` (BR-004); **`prefix=""`** not treated as root (**FR-011**).

### NFR-001 — Typecheck

**VC-011: Static typecheck clean**

- **Scenario:** CI typecheck job.
- **Input/State:** `uv run ty check` (or documented fallback).
- **Expected Result:** Exit code 0.
- **Failure Mode Covered:** Missing annotations, wrong types on app factory.

### NFR-002 — Ruff

**VC-012: Ruff clean**

- **Scenario:** CI lint/format check.
- **Input/State:** `uv run ruff check .` and `uv run ruff format --check .`.
- **Expected Result:** Exit code 0.
- **Failure Mode Covered:** Style/lint regressions.

### NFR-003 — Test duration

**VC-013: Fast interface suite**

- **Scenario:** Single-thread local run of interface tests for this spec.
- **Input/State:** Cold start acceptable; measure wall time.
- **Expected Result:** Completes < 30 seconds.
- **Failure Mode Covered:** Accidental sleeps, network calls in tests.

### NFR-004 — uv and Python pin

**VC-014: Single Python version pinned, uv-driven environment**

- **Scenario:** New contributor or CI runner sets up the project from a fresh clone.
- **Input/State:** Repository at HEAD with `.python-version`, `pyproject.toml`, and `uv.lock` committed.
- **Expected Result:**
  - `.python-version` contains exactly the string `3.12` (no range, no patch-level pin) — verifiable by reading the file.
  - `pyproject.toml` contains `requires-python = ">=3.12"` under `[project]`.
  - `uv sync --frozen` succeeds (lockfile is consistent with `pyproject.toml`).
  - `uv run pytest -q` runs against the locked environment without re-resolution.
- **Failure Mode Covered:** Drift between dev and CI Python; missing `.python-version`; `requires-python` allowing older interpreters than the dev pin; lockfile out of sync with `pyproject.toml`.

### NFR-005 — Dependency floors and lockfile

**VC-020: Dependency floors declared and lockfile authoritative**

- **Scenario:** Reproducible install across dev and CI without opportunistic version resolution.
- **Input/State:** `pyproject.toml` + `uv.lock` at HEAD.
- **Expected Result:**
  - `pyproject.toml` declares `fastapi` and `starlette` in `[project].dependencies` **each with an explicit lower-bound specifier** (e.g. `fastapi>=…`, `starlette>=…`); no bare names without a lower bound. Test extras (e.g. `pytest`, `httpx`) declared in `[dependency-groups]` / `[project.optional-dependencies]` follow the same rule.
  - `uv.lock` exists at the repo root and is in sync with `pyproject.toml` (`uv lock --check` exits 0, or equivalent `uv sync --frozen` succeeds without modification).
  - Running CI with the lockfile frozen reproduces the same resolved versions across runs.
- **Failure Mode Covered:** Floor-less dependencies allowing breakage from upstream major bumps; missing `uv.lock`; lockfile drift; CI silently resolving newer versions than dev.

### End-to-end acceptance

**VC-021: Full public-surface smoke test**

- **Scenario:** A single test exercises the entire library public surface in one run: default-prefix app, prefixed app, instance-id stability and uniqueness, health body, metrics emptiness, and lifespan wiring (via `TestClient` context-manager). When this VC is green, the library is acceptable for a host to depend on.
- **Input/State:**

  ```python
  from fastapi.testclient import TestClient
  from langgraph_runnable_server import create_app

  default_app = create_app()                      # default prefix → /health, /metrics
  prefixed_app = create_app(prefix="/api")        # prefixed → /api/health, /api/metrics

  default_id_before = default_app.state["instance_id"]
  prefixed_id_before = prefixed_app.state["instance_id"]
  ```

- **Expected Result (all must hold in one test):**
  1. **Instance IDs are non-empty strings** (FR-001):
     `isinstance(default_id_before, str) and len(default_id_before) > 0`; same for `prefixed_id_before`.
  2. **Instance IDs are distinct across apps** (FR-002, A-001):
     `default_id_before != prefixed_id_before`.
  3. **Default app endpoints work and lifespan runs** (FR-003, FR-004, FR-005, BR-001, BR-003, BR-004):
     ```python
     with TestClient(default_app) as client:
         h = client.get("/health")
         m = client.get("/metrics")
         assert h.status_code == 200
         assert h.content == b"ok"  # BR-001: exactly three octets, no trailing newline
         assert h.headers["content-type"].startswith("text/plain")
         assert m.status_code == 200
         assert m.content == b""
         # Instance ID stable across requests on the same app (FR-001):
         assert default_app.state["instance_id"] == default_id_before
     ```
  4. **Prefixed app endpoints work under the chosen `{base}`** (FR-010, FR-011, BR-004, BR-005, VC-018):
     ```python
     with TestClient(prefixed_app) as client:
         h = client.get("/api/health")
         m = client.get("/api/metrics")
         assert h.status_code == 200
         assert h.content == b"ok"
         assert m.status_code == 200
         assert m.content == b""
         # Routes are *not* served at the un-prefixed path on the prefixed app:
         assert client.get("/health").status_code == 404
         assert client.get("/metrics").status_code == 404
     ```
  5. **Instance ID stable after all requests** (FR-001):
     `default_app.state["instance_id"] == default_id_before` and `prefixed_app.state["instance_id"] == prefixed_id_before`.
  6. **`__all__` discipline holds** (FR-009):
     `import langgraph_runnable_server as p; assert p.__all__ == ["create_app"]`.

- **Failure Mode Covered:** Single-test catch for regressions in FR-001, FR-002, FR-003, FR-004, FR-005, FR-009, FR-010, FR-011, FR-014, BR-001, BR-003, BR-004, BR-005, BR-006, A-001. This VC overlaps with VC-001/002/004/005/015/017/018 intentionally — its purpose is to verify the **full surface composes correctly**, not just the individual contracts in isolation.
- **Note:** This is an **acceptance** criterion; per-requirement VCs (**VC-001**–**VC-020**, **VC-022**) remain authoritative for their respective FRs/BRs/NFRs. VC-021 must remain a single test function so a failure here is a single, high-signal red flag for "library is not host-ready".

## Open Questions

None.

## Assumptions

- **A-001:** Hosts may call **`create_app()`** multiple times in one process; each returned app has its **own** `app.state["instance_id"]` (VC-002).
- **A-002:** `TestClient` from Starlette/FastAPI is acceptable for all interface VCs (no separate HTTP server requirement); the client wraps the **`FastAPI`** instance returned by **`create_app`** with **no** additional Starlette mount prefix unless testing host integration (out of scope).
- **A-003:** English **`ok`** lowercase is the only supported liveness payload at **`{base}/health`** for any valid **`prefix`**; probes must use the URL including **`prefix`** (BR-004).
- **A-006:** For **FR-004** / **VC-004**, a **`Content-Type`** of **`text/plain`** or **`text/plain; charset=utf-8`** (case-insensitive parameter name/value as appropriate) is acceptable; the body rules (**BR-001**) are unchanged.
- **A-004:** The team adopts **uv**, **ruff**, and **ty** as in the Python coding standard; if `ty` is unavailable, a documented **mypy strict** fallback satisfies NFR-001 for that period only.
- **A-005:** The library is consumed as a normal dependency (`pip` / `uv add`); editable installs are sufficient for local development of the library itself.

## Changelog

| Version | Date | Notes |
|---------|------|-------|
| 1.0 | 2026-05-11 | Initial spec from skeleton requirements; aligned with package layout and pytest interface layer. |
| 1.1 | 2026-05-11 | Reframed as installable **library** with `create_app()`, `py.typed`, `__all__`, host actor, BR-004 path contract, FR-009/FR-010, VC-015–VC-017. |
| 1.2 | 2026-05-11 | **Single public API:** `create_app(prefix: str = "/")` only; `__all__ = ["create_app"]`; routes under `{base}`; FR-011/BR-005; instance ID via **`app.state`** only; VC-018/VC-019; tests for default + prefixed paths. |
| 1.3 | 2026-05-11 | Resolved Open Question 1: distribution name **`langgraph-runnable-server`**, import name **`langgraph_runnable_server`**; replaced all `<library_package>` placeholders with the resolved import name across FRs, NFRs, layout, and VCs. |
| 1.4 | 2026-05-11 | Added optional `lifespan: Lifespan[FastAPI] \| None = None` argument to `create_app` (FR-010); rewrote FR-003 so the library always wires a lifespan (host-supplied verbatim, or built-in no-op default); added FR-013 for the lifespan contract; extended FR-008 to require an interface test exercising a host-supplied lifespan; tightened VC-003 into a two-part automatable criterion (default lifespan registered + host-supplied lifespan runs startup and shutdown via `TestClient` context). Resolves review action item 2. |
| 1.5 | 2026-05-11 | Pinned dev/CI Python to a single version (**3.12**) via `.python-version`, with `requires-python = ">=3.12"` floor in `pyproject.toml`; rewrote NFR-004 accordingly; added NFR-005 requiring explicit lower bounds on `fastapi`/`starlette` (and test deps) plus a committed `uv.lock` enforced in CI via `uv sync --frozen`; added VC-020 and tightened VC-014 to verify both the Python pin and the lockfile contract; updated layout diagram to show `uv.lock` and `.python-version` as committed artifacts. Resolves review action item 3. |
| 1.6 | 2026-05-11 | Resolves review action items 4–7. **(4)** Rewrote VC-009 from a PR-checklist fallback into a concrete automated test: patches `socket.socket`/`connect`, `httpx.Client.send`, `httpx.AsyncClient.send`, and `urllib.request.urlopen` to explode if invoked, then asserts `GET /health` and `GET /api/health` succeed without touching any of them. **(5)** Added VC-021, an end-to-end acceptance test that exercises default + prefixed apps, instance-id stability and uniqueness, `/health`/`/metrics` bodies and statuses, 404 on un-prefixed paths of a prefixed app, lifespan via `TestClient` context, and `__all__` discipline in a single test. **(6)** Added a one-line rationale to FR-001 explaining `app.state["instance_id"]` over a module-level singleton (per-app isolation, no import-time side effects, multi-app processes). **(7)** Reordered the FR sections so "Package layout and discoverability" (FR-006/007) sits immediately before "Public API (library)" (FR-009/010/011/012), making the two groups contiguous. Did **not** renumber FRs or VCs — that remains a separate, traceability-impacting change if ever desired. |
| 1.7 | 2026-05-11 | Resolved former open questions on **prefix** and health body. **`prefix=""`** (and whitespace-only after trim) normalizes to root **`"/"`**; **`//`** and other **FR-011** violations raise **`ValueError`**; minimum “valid URL path” rule uses RFC 3986 **`pchar`**. **BR-001** / **FR-004** now forbid any trailing newline after **`ok`**. **A-006** documents acceptable **`text/plain`** charset. **BR-004**, **FR-008**, **FR-010**, **FR-011**, **BR-005** updated; **VC-004**, **VC-008**, **VC-017**, **VC-019** updated; Open Questions cleared. |
| 1.8 | 2026-05-11 | **FR-014** / **BR-006**: any non-**GET** to **`{base}/health`** or **`{base}/metrics`** must return **404** (not **405**). **VC-022** and **FR-008** test hook; HTTP table section and **VC-021** failure-mode list updated. |
