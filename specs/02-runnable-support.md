# Specification: LangGraph Runnable HTTP Surface, Metrics, and Request Logging

| Field | Value |
|-------|-------|
| Version | 1.2 |
| Last Updated | 2026-05-11 |
| Status | Draft |
| Depends on | `specs/01-fastapi-server.md` (FastAPI bootstrap, `{base}` for probes, prefix rules, `create_app`) |

## Purpose

Extend the library so a **host** can mount **one or more** LangChain- or LangGraph-compatible **`Runnable`** instances behind a stable HTTP API: **`ainvoke`** and **`abatch`** only, with **one structured log event per request** (wide-event style via **structlog**), **Prometheus metrics** broken down by **which runnable** and **which endpoint**, and **no reliance on framework default access logging** for request visibility. Operators and SREs benefit from scrape-ready metrics and consistent logs; application authors benefit from a single factory that composes **`create_app`** (health, metrics, lifespan, instance identity) with runnable routes.

## Scope

### In

- New public factory **`create_runnable_app`** that **calls `create_app`** (spec 01) and **registers additional routes** for each configured runnable.
- **Runnable map:** argument **`runnables: dict[str, Runnable]`** whose **keys** are logical agent names (URL segments) and **values** are runnable instances exposing **`ainvoke`** and **`abatch`** (async) compatible with the LangChain **`Runnable`** protocol for those two operations. Arbitrary `Mapping` subtypes are **not** accepted (see **FR-105**).
- **Mount prefix for runnables:** string argument **`prefix`** (same semantic role as the user-facing example **`prefix="/agents"`**) normalized with the **same rules as `prefix` in FR-011** of spec 01 (trim, reject `//`, leading `/`, trailing slash stripping, `pchar` validation). Effective paths:

  `{runnables_base}/{runnable_key}/invoke`  
  `{runnables_base}/{runnable_key}/batch`

  where **`{runnables_base}`** is empty when the normalized runnable mount is root **`"/"`**, otherwise the normalized string **without** trailing slash (same convention as **`{base}`** for probes). Example: `prefix="/agents"` and keys `agent1`, `agent2` yield **`/agents/agent1/invoke`**, **`/agents/agent1/batch`**, **`/agents/agent2/invoke`**, **`/agents/agent2/batch`** when the ASGI app is mounted at the URL root.

- **HTTP methods:** **`POST`** only on **`invoke`** and **`batch`** paths for this release (other methods are out of scope for behavior beyond what FastAPI/Starlette returns by default).
- **Request/response serialization:** **JSON** request bodies and **JSON** response bodies for successful handler paths, with explicit error bodies where status is **≥ 400** (shape implementation-defined but stable and documented in code).
- **Prometheus exposition** on **`GET {probe_base}/metrics`** for apps created via **`create_runnable_app`**, including metrics labeled by **runnable key** and **endpoint** (`invoke` | `batch`): **request count**, **request duration**, **error response count**, **response size**, **request size** (definitions under **Business Rules** and **NFR**).
- **Structured logging:** exactly **one** **structlog**-emitted **wide event** per HTTP request that reaches the ASGI application (including probes and runnable routes), with a documented **minimum field set**; **FastAPI/Starlette/Uvicorn default access-style logging must not** be the **only** or **duplicate** source of per-request lines for hosts that follow the library’s deployment note (**NFR-010**).
- **Public API:** extend **`__all__`** to export **`create_app`** and **`create_runnable_app`** (and no other names).
- **Automated verification** via **pytest** (interface-level tests with **`TestClient`** and metric scrape assertions where practical).

### Out

- Any runnable HTTP operations other than **`invoke`** (backed by **`ainvoke`**) and **`batch`** (backed by **`abatch`**), including streaming, `astream`, `astream_events`, `get_graph`, OpenAPI-only helpers, or LangGraph Cloud-specific routes.
- **Authentication, authorization,** quotas, and per-tenant isolation (hosts add middleware or reverse proxies).
- **Persistence**, checkpointing, thread management, or conversation IDs (unless passed inside JSON bodies by the host's chosen runnable).
- **Request body size limits, request timeouts, and concurrency caps:** the library does not enforce a maximum request body size, a per-request timeout, or a maximum number of in-flight requests. Hosts **Must** enforce these via a reverse proxy (e.g. nginx `client_max_body_size`, gateway timeouts) or a host-side middleware (see **NFR-111**).
- **Custom Prometheus registries** beyond the per-app `CollectorRegistry` documented in this spec (one registry per `FastAPI` app object, owned by the library; no host-supplied registry injection in this release).
- **Log shipping agents,** retention policies, and **PII redaction** beyond documenting which fields exist and that values may contain caller-supplied data.
- Changing **`create_app`-only** apps (spec 01) to emit non-empty **`/metrics`** in this release **is not required**; only apps produced through **`create_runnable_app`** must satisfy the new **`/metrics`** contract in this document.

### Amendments to spec 01

This spec deliberately amends two normative constraints from spec 01 for apps produced via **`create_runnable_app`** (apps produced via plain **`create_app`** are unchanged):

- **`__all__` is extended.** Spec 01 **FR-009** fixes `__all__ = ["create_app"]`. This spec extends `__all__` to **`["create_app", "create_runnable_app"]`** (see **FR-112**). No other names are added.
- **`/metrics` body is replaced for `create_runnable_app` apps.** Spec 01 **FR-005** / **BR-003** require `GET {base}/metrics` to return an empty body for apps from `create_app`. For apps from **`create_runnable_app`**, **FR-120** replaces that with Prometheus text exposition. `create_app`-only apps are unaffected.

## Actors

| Actor | Description | Permissions |
|-------|-------------|-------------|
| **Host application developer** | Calls **`create_runnable_app`**, passes **`runnables`**, runnable **`prefix`**, and optional **`create_app`** arguments (**`create_app_prefix`** for probes, **`lifespan`**). Owns process and ASGI server config (e.g. Uvicorn `access_log`). | Chooses prefixes so probe and runnable URLs match deployment; supplies runnable instances and valid keys. |
| **HTTP API client** | Sends **`POST`** to **`…/invoke`** or **`…/batch`** with JSON bodies. | No library-enforced auth in scope; may read JSON responses and status codes. |
| **Operator / SRE** | Scrapes **`GET {probe_base}/metrics`**, tails structured logs. | Reads metrics text and log fields documented for the wide event. |
| **Orchestrator** | Same as spec 01 for **`GET {probe_base}/health`**. | Unchanged. |

## Functional Requirements

MoSCoW: **Must** / **Should** / **Could** / **Won’t** (this release).

### Factory and composition

- **FR-101 (Must)** — Given **`create_runnable_app`** is called with valid arguments, when it returns, then the return value is a **`FastAPI`** instance that satisfies all applicable requirements from **`create_app`** in spec 01 (**FR-001**–**FR-014**, **FR-011** normalization for the **probe** `prefix`) for the **`create_app(prefix=..., lifespan=...)`** arguments the host passed through (see **FR-110**), **and** registers runnable routes as in **FR-102**–**FR-104**.

- **FR-102 (Must)** — Given a non-empty key **`k`** in **`runnables`** and normalized runnable mount **`{runnables_base}`**, when a **`TestClient`** targets the app at the ASGI root, then **`POST {runnables_base}/{k}/invoke`** is routed to a handler that **awaits** **`runnables[k].ainvoke(...)`** exactly once per successful request path for that handler (see **BR-101** for argument mapping), and the HTTP response status is **200** when the runnable completes without raising and the output is JSON-serializable per **BR-103**.

- **FR-103 (Must)** — Given key **`k`**, when **`POST {runnables_base}/{k}/batch`** is issued per **FR-102** path rules, then the handler **awaits** **`runnables[k].abatch(...)`** exactly once per successful request path (see **BR-102**), and the HTTP response status is **200** on successful completion and JSON-serializable output per **BR-103**.

- **FR-104 (Must)** — Given two distinct keys **`k1`** and **`k2`** in **`runnables`**, when requests target **`…/k1/invoke`** and **`…/k2/invoke`**, then each request invokes **only** the runnable bound to its key (**no cross-routing**).

- **FR-105 (Must)** — **`runnables` Must be a `dict[str, Runnable]`.** Arbitrary `Mapping` implementations are **not** accepted; if the argument is not a `dict` (checked with `isinstance(runnables, dict)`), the factory **raises `TypeError`** before constructing routes. *Rationale:* `dict` already guarantees one key per runnable; rejecting other `Mapping` types eliminates the duplicate-key edge case at the type boundary.

- **FR-106 (Must)** — Given **`runnables`** is an **empty `dict`**, when **`create_runnable_app`** returns, then the app **still** exposes probe routes per spec 01 and **does not** register any runnable routes; **`GET {probe_base}/metrics`** still returns Prometheus text per **FR-120** with the library's metric families declared but zero samples (i.e. `..._total 0` / histogram `_count 0` lines for any pre-declared label-less families, or simply no series for label-bearing families until traffic — both forms are valid Prometheus text).

- **FR-107 (Must)** — Given a key string that is **not** a valid single URL path segment under **BR-107** (e.g. contains **`/`**, empty string, exceeds 64 characters, or contains characters outside `[A-Za-z0-9._-]`), when **`create_runnable_app`** runs, then the factory **raises `ValueError`** before route registration.

- **FR-108 (Must)** — Given the host passes runnable **`prefix`** + key combinations whose normalized full path **collides** with `{probe_base}/health` or `{probe_base}/metrics`, when **`create_runnable_app`** runs, then the factory **raises `ValueError`** with a message that names the colliding path. The collision check **Must** compare full normalized paths (`{runnables_base}/{key}/invoke` and `{runnables_base}/{key}/batch` against `{probe_base}/health` and `{probe_base}/metrics`), not just prefixes.

- **FR-109 (Must)** — Given a runnable raises an **exception** during **`ainvoke`** or **`abatch`**, when the handler completes, then the HTTP response has status **500** (unless a more specific **4xx** is mandated by **BR-104** for bad input), the response body is JSON of the form **`{"detail": "<message>"}`** (key fixed at **`detail`** to align with FastAPI's `HTTPException` default), and the **error counter** metric for that runnable and endpoint increments (**BR-201**). The exception message Must Not include a stack trace; structured logs (per **FR-130**) capture the exception type and traceback for operators.

- **FR-110 (Must) — Factory signature.** **`create_runnable_app`** **Must** be a keyword-only callable with this exact public signature:

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

  No positional arguments are accepted (all parameters are keyword-only). Normative call shape: **`create_runnable_app(prefix="/agents", runnables={...})`**. The **`metrics_namespace`** argument controls the Prometheus metric name prefix per **FR-123**.

- **FR-111 (Must) — Prefix normalization reuse.** The runnable **`prefix`** argument **Must** be normalized using the **same procedure as spec 01 FR-011** (trim, reject `//`, require leading `/`, strip trailing slashes, RFC 3986 `pchar` validation). Violations **Must** raise `ValueError` before any `FastAPI` instance is returned. **`{runnables_base}`** is derived from runnable **`prefix`** only; **`{probe_base}`** is derived from **`create_app_prefix`** only.

- **FR-112 (Must) — `lifespan` and `create_app_prefix` forwarding.** **`create_runnable_app`** **Must** forward **`create_app_prefix`** to `create_app(prefix=create_app_prefix, ...)` verbatim and **`lifespan`** to `create_app(lifespan=lifespan, ...)` verbatim. The library **Must Not** wrap, compose, or replace the host-supplied **`lifespan`** (same contract as spec 01 FR-003 / FR-013). **`__all__`** at the package root **Must** equal **`["create_app", "create_runnable_app"]`** (extending spec 01 FR-009 — see "Amendments to spec 01").

### Metrics

- **FR-120 (Must)** — Given an app from **`create_runnable_app`**, when **`GET {probe_base}/metrics`** is called, then the response status is **200**, the response **`Content-Type`** is exactly **`text/plain; version=0.0.4; charset=utf-8`** (the value of `prometheus_client.CONTENT_TYPE_LATEST`), and the body is **always** valid Prometheus text exposition for the scrape (no parse errors in **`promtool check metrics`** for the defined metric families). The body is **non-empty** as soon as the library's metric families are declared at factory time — even with zero traffic, the scrape contains the `HELP` / `TYPE` lines for each registered family.

- **FR-121 (Must)** — For each **`POST`** to **`invoke`** or **`batch`** on a registered runnable key, metrics **Must** record: **total requests**, **duration**, **total errors** (HTTP status **≥ 400**), **request body size in bytes**, and **response body size in bytes**, each **broken down by labels** **`runnable`** (the map key) and **`endpoint`** (`invoke` or `batch`). Exact metric **names** and **types** (counter vs histogram) are listed under **Data & Interfaces**; implementers **Must** not omit a dimension if a request completed routing to that handler.

- **FR-122 (Must) — Per-app Prometheus registry.** Each app produced by **`create_runnable_app`** **Must** own a **dedicated `prometheus_client.CollectorRegistry` instance**, created at factory time, stored at **`app.state["metrics_registry"]`**, and used exclusively for the metric families listed in **Data & Interfaces**. The library **Must Not** register its metrics on the `prometheus_client` default global registry (`prometheus_client.REGISTRY`). **`GET {probe_base}/metrics`** **Must** generate exposition text from this per-app registry (via `prometheus_client.generate_latest(app.state["metrics_registry"])`). *Rationale:* allows two or more `create_runnable_app` calls in the same process without `Duplicated timeseries` registration errors; gives tests a clean registry per app object; aligns with spec 01 A-001's "multiple apps per process" assumption.

- **FR-123 (Must) — Configurable Prometheus namespace.** The **`metrics_namespace`** argument of **`create_runnable_app`** (default **`"langgraph_runnable_server"`**) **Must** be used as the `namespace=` argument passed to every `prometheus_client.Counter` / `Histogram` constructor for the families listed in **Data & Interfaces**. The resulting metric names are **`{metrics_namespace}_{base_name}`** where `{base_name}` is the family base from the table (e.g. `requests_total`, `request_duration_seconds`). Validation rules at factory time:
  - The default value `"langgraph_runnable_server"` produces the original metric names (e.g. `langgraph_runnable_server_requests_total`) — i.e. behavior is backward-compatible when the argument is omitted.
  - **`metrics_namespace`** **Must** be a `str`. If not, the factory raises `TypeError`.
  - If non-empty, **`metrics_namespace`** **Must** match the regex **`^[a-zA-Z_][a-zA-Z0-9_]*$`** (Prometheus metric name fragment — letters, digits, and underscore; cannot start with a digit; `:` is reserved for recording rules and is **not** accepted in this argument). Violations raise `ValueError`.
  - **`metrics_namespace`** **May** be the empty string **`""`**, in which case no prefix is prepended (metric names become `requests_total`, `request_duration_seconds`, etc.). This is useful when the host enforces a namespace at scrape time.
  - The chosen `metrics_namespace` **Must** be stored at **`app.state["metrics_namespace"]`** so observability / tests can read it back.

  *Rationale:* hosts deploying multiple services in one Prometheus instance often need to control the metric namespace to avoid collisions with other services or to match an org-wide naming convention. Keeping the existing prefix as the default preserves backward compatibility.

### Logging

- **FR-130 (Must)** — Given **any** HTTP request (probes, metrics, runnable routes), when the response is ready to send, then **exactly one** structured log record is emitted via **structlog** at **INFO** (or documented level) whose **bound context** includes at minimum the fields in **BR-301**, when that request passed through the library’s request logging middleware.

- **FR-131 (Must)** — Given the library’s ASGI stack for **`create_runnable_app`**, when configured per **NFR-010**, then **Uvicorn access log** lines are **disabled** for that server instance **and** the library **does not** add a second access logger that duplicates the same path/method/status line in default format (wide structlog event remains the **canonical** per-request line).

- **FR-132 (Must)** — Given **FastAPI**’s default logging hooks that would emit **per-request** duplicate access lines in typical dev setups, when the documented **host/settings** from **NFR-010** are applied, then **only** the structlog wide event satisfies **FR-130** for request-level visibility (no additional **FastAPI**-owned access line for the same request).

## Business Rules

- **BR-101** — **`POST …/invoke` body:** JSON object **Must** accept **`"input"`** as the value passed as the **first** argument to **`ainvoke`**, and optional **`"config"`** as the second argument if present; if **`"input"`** is absent, **`ValueError`** at validation → **422** with JSON error body. *Rationale:* explicit envelope avoids ambiguity between runnable state dicts and metadata. *Exceptions:* none.

- **BR-102** — **`POST …/batch` body:** JSON object **Must** contain **`"inputs"`** whose value is a **JSON array** (order preserved) passed to **`abatch`** as the first positional argument. Optional **`"config"`** is forwarded **verbatim** to `abatch` as the `config=` keyword argument; the library does not interpret, normalize, or length-check `config`. If the runnable's `abatch` rejects the value (e.g. raises `TypeError` because it doesn't support per-item config arrays), the resulting exception is handled per **FR-109** and counted as a **500**. An empty `"inputs": []` is **valid** and **Must** produce a **200** response with body **`[]`** without invoking the runnable (no call to `abatch`). *Rationale:* one stable batch contract, no library-level guessing of runnable capabilities. *Exceptions:* none.

- **BR-103** — **Serialization:** Successful **`ainvoke`** / **`abatch`** results **Must** be encoded as JSON via **FastAPI's `jsonable_encoder`** (Pydantic v2 JSON mode). This handles JSON-native types, **`pydantic.BaseModel`** subclasses (including LangChain message types such as **`AIMessage`** / **`HumanMessage`** / **`ToolMessage`**, which are `BaseModel` subclasses in modern `langchain-core`), **`dataclasses`**, **`datetime`** / **`date`** / **`time`**, **`UUID`**, **`Enum`**, **`Path`**, **`Decimal`**, and **`set`** / **`frozenset`** (encoded as JSON arrays) per the documented `jsonable_encoder` contract. **No project-side `default=` fallback is configured** and **LangChain's `dumps` / `dumpd` envelope is not used** (clients receive plain JSON, not the `{"lc": 1, "type": "constructor", ...}` shape). Values that `jsonable_encoder` cannot encode propagate as exceptions and are handled per **FR-109**. *Rationale:* uses the FastAPI-native serialization path; handles LangChain Pydantic message types out of the box; avoids forcing clients to understand LangChain's serialization envelope; keeps the JSON contract narrow and predictable. *Exceptions:* types that cannot serialize return **500** and count as errors.

- **BR-104** — **Client errors:** Malformed JSON, missing required keys (`input` for invoke, `inputs` for batch), wrong root type (e.g. JSON array at the root for `invoke`), or a **`Content-Type`** other than **`application/json`** (or a missing `Content-Type` when a body is present) **Must** yield **422** with a FastAPI-style error body and **Must** increment the **error** metric (**≥ 400**). A JSON value of **`null`** is a **valid** payload for `"input"` and Must Not produce a 422. *Rationale:* aligns with FastAPI validation semantics; explicit handling of edge cases that real clients hit. *Exceptions:* none.

- **BR-105** — **Probe routes** remain **GET-only** per spec 01 (**FR-014**); runnable routes are **POST-only** for **invoke**/**batch** in this release. Any non-**POST** request to **`{runnables_base}/{key}/invoke`** or **`{runnables_base}/{key}/batch`** **Must** return **405** (FastAPI's default), not 404, because the path is registered for a different method. *Rationale:* clear method semantics; distinguishes "wrong method on a real route" from "no such route." *Exceptions:* none.

- **BR-106** — **`GET {probe_base}/metrics`** on **`create_runnable_app`** apps **Must Not** increment any of the **invoke/batch** metric families listed in **Data & Interfaces** for this app’s effective Prometheus name prefix **`{ns}`** (same meaning as in that section: derived from **`metrics_namespace`**, including the bare family names when **`metrics_namespace`** is **`""`** per **FR-123**). Those families are reserved for `invoke` / `batch`. The scrape path does not pass through the runnable middleware that increments these counters. No separate `metrics_scrapes_total` counter is required in this release. *Rationale:* scrape traffic must not pollute SLO metrics.

- **BR-107** — **Runnable map keys** **Must** match the regular expression **`^[A-Za-z0-9._-]{1,64}$`** (length **1** to **64** inclusive). Specifically: length **0** (empty string) and length **≥ 65** **Must** be rejected; lengths **1** and **64** **Must** be accepted. *Rationale:* safe URL segment and Prometheus label value. *Exceptions:* none unless **Open Question OQ-001** is resolved to allow Unicode.

- **BR-108** — **Client disconnect / cancellation.** If the HTTP client disconnects (or the request is otherwise cancelled) while `ainvoke` / `abatch` is awaiting, the handler **Must** allow the resulting `asyncio.CancelledError` to propagate so the runnable is cancelled cooperatively. Metrics and the structlog wide event **Should** still be emitted on a best-effort basis (recording the cancellation as a non-200 status — implementations may use status **499** in logs/metrics; the HTTP response is moot because the client is gone). *Rationale:* hold-on resources; visibility into client churn. *Exceptions:* none.

- **BR-109** — **Content-Type and body shape.** Requests to `invoke` / `batch` **Must** declare **`Content-Type: application/json`** (FastAPI requirement). Missing or non-JSON `Content-Type` with a non-empty body yields **422** per **BR-104**. A request **Must** carry a JSON object at the body root for both endpoints; a JSON array, string, number, boolean, or `null` at the body root yields **422**. *Rationale:* unambiguous envelope. *Exceptions:* none.

- **BR-201** — **Error metric:** An **error** is any response with HTTP status **`status_code >= 400`**. *Rationale:* operators see client and server failures. *Exceptions:* none.

- **BR-202** — **Duration** is measured **wall time in seconds** from when the **request enters the library's logging/metrics middleware** until **response headers are sent** (equivalently: `time.perf_counter()` taken on middleware entry and exit). The histogram **Must** use **exactly** the buckets **`(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)`** seconds plus the implicit **`+Inf`** bucket. *Rationale:* SLO-friendly bucket set covering sub-millisecond probe latency through 10-second LLM completions. *Exceptions:* none.

- **BR-203** — **Request size** is the **number of bytes** of the request body as read for the handler. If the body is not fully consumed (early validation error before parsing), the library **Must** use the request's `Content-Length` header value if present and well-formed; if `Content-Length` is absent or malformed (e.g. chunked transfer encoding with no declared length), the implementation **Must omit** the sample — i.e. do not record a histogram observation for that request. The wide-event **`request_size_bytes`** field (**BR-301**) **Must** follow the **same** omit-vs-present rule: **omitted** when this paragraph requires omitting the request-size histogram sample; **present** as a non-negative integer otherwise. **Response size** is the **number of bytes** in the response body as serialized by FastAPI (after JSON encoding). *Rationale:* deterministic bytes-out, with an explicit "omit" rule for the one edge case where bytes-in is unknowable, rather than fabricating an estimate; logs and metrics stay consistent on request size.

- **BR-301** — **Wide event minimum fields:** **`http.method`**, **`http.route`** (the registered route template, e.g. `/agents/{key}/invoke`, **not** the resolved URL), **`http.status_code`**, **`duration_ms`** (float, milliseconds), **`instance_id`** (from `app.state["instance_id"]`, per spec 01 FR-001), **`runnable`** (the resolved key string for runnable routes; **omitted** for probes), **`endpoint`** (`"invoke"` or `"batch"` for runnable routes; **omitted** for probes), **`request_size_bytes`** (non-negative integer when present; **omitted** when **BR-203** requires omitting the request-size measurement — see **BR-203** for the histogram-aligned rule), **`response_size_bytes`** (non-negative integer; **Must** always be present; **Must Not** be omitted), **`trace_id`** (parsed from the inbound **W3C `traceparent`** HTTP header per `https://www.w3.org/TR/trace-context/`; specifically the 32-hex `trace-id` component. **Omitted** if no `traceparent` header is present or the header fails W3C format validation). *Rationale:* one line per request with operable dimensions; fields use `omit` instead of `null` to keep log volume tight and queries simple; response body length is always defined on the logging path, unlike the rare unknowable request-body case. *Exceptions:* optional extra fields **Could** be added without breaking VC.

## Non-Functional Requirements

- **NFR-101** — **Typing:** `ty check` passes with **zero** errors on **`src/`** after adding **`create_runnable_app`** and route modules; public symbols fully typed.

- **NFR-102** — **Lint/format:** `ruff check` and `ruff format --check` pass.

- **NFR-103** — **Tests:** Runnable interface tests complete in **under 60 seconds** on a typical developer machine (single process).

- **NFR-104** — **Dependencies:** `pyproject.toml` adds **`langchain-core`**, **`structlog`**, and **`prometheus-client`** to `[project].dependencies`, each with an explicit lower-bound specifier (`langchain-core>=…`, `structlog>=…`, `prometheus-client>=…`) set to the latest stable release at first implementation, per the NFR-005 policy in spec 01. **`langchain-core`** (not `langgraph`) is the chosen umbrella — `langchain-core` provides the `Runnable` protocol, message types, and `jsonable_encoder`-compatible Pydantic models, and pulls in fewer transitive deps than `langgraph`. Hosts that use LangGraph add `langgraph` themselves; the library remains framework-neutral. **`uv.lock`** updated and **CI** uses **`uv sync --frozen`** per spec 01 NFR-005.

- **NFR-105** — **Metric cardinality:** Label combinations (**`runnable`**, **`endpoint`**) **Must** be bounded by the **number of keys** in **`runnables`** times **2**; hosts with dynamic keys are out of scope (**Won’t**).

- **NFR-106** — **Scrape parseability:** After a smoke **`POST`** to each registered endpoint, **`GET {probe_base}/metrics`** body **Must** parse with **`promtool check metrics`** with **exit code 0** when the optional tool is present in CI; otherwise a pytest assertion **Must** verify each required metric **family** appears at least once.

- **NFR-107** — **Log volume:** For **N = 100** sequential **`GET /health`** requests, the test log sink captures **exactly N** wide events at **INFO** for those requests (± 0), and **no** duplicate second line per request from a library-added stdlib **`logging`** access handler. *Measurable in automated test with caplog or custom structlog processor.*

- **NFR-108** — **FastAPI logging:** Library documentation **Must** state that **`create_runnable_app`** expects hosts to configure **uvicorn** with **`access_log=False`** and to avoid enabling duplicate access middleware; conformance tests **Must** use that setting when spinning a live server if any integration test does so. References to **NFR-010** elsewhere in this spec resolve to this NFR (FR-130 / FR-131 / FR-132).

- **NFR-109** — **Python:** Remains **3.12** pinned per spec 01 (**NFR-004** there).

- **NFR-110** — **Documentation string:** Module docstring or package README section (code-adjacent, not a new standalone doc file unless the repo already uses one) **Must** list **metric names**, **label names**, and **minimum log fields**.

- **NFR-111 — Security boundary (request limits).** The library does **not** enforce a maximum request body size, a request timeout, JSON nesting depth limits, or a maximum number of in-flight requests. Library documentation **Must** state explicitly that:
  - Hosts **Must** front the library with a reverse proxy or gateway that enforces a body size cap (e.g. nginx `client_max_body_size`) and request timeout suitable for the expected runnable payload sizes.
  - FastAPI / Starlette inherit Python's default JSON parser, which does **not** impose nesting limits; hosts handling adversarial input **Should** add a guard at the proxy or via a middleware.
  - The library does not propagate cancellation deadlines into runnables beyond the cooperative `asyncio` cancellation described in **BR-108**.

  *Rationale:* keeps the library narrowly scoped and avoids shipping a reverse-proxy-shaped feature; makes the host's responsibility explicit so reviewers don't assume the library is hardened against large or malicious payloads.

## Data & Interfaces

### HTTP — Runnable routes

Let **`{runnables_base}`** be the normalized runnable **`prefix`** argument to **`create_runnable_app`** (same empty-vs-root rules as **`{base}`** in spec 01).

| Method | Path pattern | Success status | Body |
|--------|----------------|----------------|------|
| POST | `{runnables_base}/{key}/invoke` | 200 | JSON: runnable output |
| POST | `{runnables_base}/{key}/batch` | 200 | JSON: runnable batch output |

**Examples** (ASGI root, `create_app_prefix="/"`, runnable `prefix="/agents"`): **`/agents/agent1/invoke`**, **`/agents/agent1/batch`**, **`/agents/agent2/invoke`**, **`/agents/agent2/batch`**.

### HTTP — Probes (unchanged semantics)

Per spec 01: **`GET {probe_base}/health`**, **`GET {probe_base}/metrics`** where **`{probe_base}`** is from **`create_app_prefix`**.

### Prometheus metric families (normative names)

All metric families are registered on the **per-app `CollectorRegistry`** at `app.state["metrics_registry"]` (see **FR-122**), not the `prometheus_client` global default registry. The metric name **prefix** is the **`metrics_namespace`** argument to **`create_runnable_app`** (default `"langgraph_runnable_server"`, see **FR-123**); the table below uses **`{ns}`** as a placeholder for that prefix.

Implementations **Must** register these **exact** metric **base** names (the names below the prefix; suffixes `_total`, `_bucket`, `_count`, `_sum` are appended by the `prometheus_client` exposition layer per type):

| Name | Type | Labels | Purpose |
|------|------|--------|---------|
| `{ns}_requests_total` | Counter | `runnable`, `endpoint` | Total requests that reached invoke/batch handler |
| `{ns}_request_duration_seconds` | Histogram | `runnable`, `endpoint` | Wall duration per **BR-202** (buckets fixed in BR-202) |
| `{ns}_errors_total` | Counter | `runnable`, `endpoint`, `http_status_class` | Errors; `http_status_class` is exactly `"4xx"` or `"5xx"` |
| `{ns}_request_size_bytes` | Histogram | `runnable`, `endpoint` | Request body size (**BR-203**) |
| `{ns}_response_size_bytes` | Histogram | `runnable`, `endpoint` | Response body size (**BR-203**) |

With the default `metrics_namespace="langgraph_runnable_server"`, the full metric names are `langgraph_runnable_server_requests_total`, `langgraph_runnable_server_request_duration_seconds`, etc. With `metrics_namespace=""`, the names are bare: `requests_total`, `request_duration_seconds`, etc. With `metrics_namespace="acme_agents"`, the names are `acme_agents_requests_total`, etc.

Histograms **Must** expose **`_count`**, **`_sum`**, and **`_bucket`** series acceptable to Prometheus 2.x text scrapes. The `Content-Type` of the `/metrics` response **Must** be **`text/plain; version=0.0.4; charset=utf-8`** (the value `prometheus_client.CONTENT_TYPE_LATEST` resolves to).

### Public Python API

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

**Example:** `create_runnable_app(prefix="/agents", runnables={"agent1": instance_agent1, "agent2": instance_agent2})` yields **`POST /agents/agent1/invoke`**, **`POST /agents/agent1/batch`**, **`POST /agents/agent2/invoke`**, **`POST /agents/agent2/batch`** when **`create_app_prefix="/"`** and the app is mounted at the ASGI root.

**Note:** The **`prefix`** here is **only** the runnable tree; probe **`{probe_base}`** comes from **`create_app_prefix`** (default **`"/"`** → **`/health`**, **`/metrics`**).

**Exports:** **`__all__ = ["create_app", "create_runnable_app"]`**.

### `Runnable` contract (behavioral)

At minimum, each value in **`runnables`** **Must** support:

- **`await runnable.ainvoke(input, config=None)`** (or signature compatible with LangChain **`Runnable`**).
- **`await runnable.abatch(inputs, config=None)`** (or the project’s documented batch async entrypoint name if **`abatch`** is provided via protocol).

Types **Should** be **`langchain_core.runnables.Runnable`** but the spec does not require importing that name at runtime if structural typing suffices.

## Verification Criteria

**VC-101: Factory composes probes and runnables**

- **Scenario:** Host creates app with two runnables under a non-root runnable prefix and default probe prefix.
- **Input/State:** `create_runnable_app(prefix="/agents", runnables={"a": r1, "b": r2})` with test doubles implementing **`ainvoke`**/**`abatch`**.
- **Expected Result:** `GET /health` returns **200** **`ok`**; `GET /metrics` returns **200** and **Prometheus** text; `POST /agents/a/invoke` hits **`r1.ainvoke`** only.
- **Failure Mode Covered:** Missing probes, wrong prefix composition, wrong runnable dispatch.

**VC-102: Path layout for invoke and batch**

- **Scenario:** Keys and prefix match user example.
- **Input/State:** `prefix="/agents"`, keys **`agent1`**, **`agent2`**.
- **Expected Result:** Paths **`/agents/agent1/invoke`**, **`/agents/agent1/batch`**, **`/agents/agent2/invoke`**, **`/agents/agent2/batch`** exist for **POST**; **not** found ( **404** ) at **`/agents/agent1/stream`**.
- **Failure Mode Covered:** Missing route, wrong HTTP method mapping, extra out-of-scope routes advertised as 200.

**VC-103: ainvoke and abatch wiring**

- **Scenario:** Successful JSON call.
- **Input/State:** Stub runnable recording arguments; valid **`{"input": {"x": 1}}`** and **`{"inputs": [{"x": 1},{"x": 2}]}`**.
- **Expected Result:** **200** JSON bodies; stub shows **`ainvoke`**/**`abatch`** called with deserialized values per **BR-101**/**BR-102**.
- **Failure Mode Covered:** Wrong arity, sync **`invoke`** used instead of async.

**VC-104: Validation errors**

- **Scenario:** Bad JSON or missing **`input`**/**`inputs`**.
- **Input/State:** Malformed body `{}` for invoke.
- **Expected Result:** **422**; error metric increments; wide log shows **`http.status_code`** 422.
- **Failure Mode Covered:** Silent **500** on validation, missing metric.

**VC-105: Runnable exception → 500**

- **Scenario:** Runnable raises **`RuntimeError`**.
- **Input/State:** Stub raises on **`ainvoke`**.
- **Expected Result:** **500** JSON error shape per **FR-109**; **`errors_total`** increments; **`requests_total`** and **`duration`** still reflect the attempt per **FR-121**.
- **Failure Mode Covered:** Unhandled exception leaks stack to client without JSON envelope.

**VC-106: Invalid keys rejected (FR-107, BR-107)**

- **Scenario:** Factory rejects malformed runnable keys at construction time.
- **Input/State:** At least the following calls Must each raise `ValueError`: key `"a/b"` (slash); key `""` (empty); key `"a" * 65` (length 65); key `"agent name"` (whitespace); key `"agent$1"` (character outside `[A-Za-z0-9._-]`).
- **Expected Result:** `ValueError` raised before any `FastAPI` instance is returned for each invalid case. Conversely: key `"a"` (length 1) and key `"a" * 64` (length 64) **Must** be accepted (factory returns a `FastAPI` app).
- **Failure Mode Covered:** Route injection, ambiguous paths, off-by-one on the length boundary.

**VC-106b: Non-`dict` `runnables` rejected (FR-105)**

- **Scenario:** Caller passes a `Mapping` that is not a `dict`.
- **Input/State:** A `collections.UserDict` or `types.MappingProxyType({"a": stub})` passed as `runnables=`.
- **Expected Result:** `TypeError` raised before route construction.
- **Failure Mode Covered:** Accepting arbitrary `Mapping` types and inheriting their corner cases.

**VC-107: Metrics labels per runnable and endpoint (default `metrics_namespace`)**

- **Scenario:** Exactly one request to `invoke` and one to `batch` for `agent1`; exactly one `invoke` for `agent2`. App is constructed with the default `metrics_namespace` (no override).
- **Input/State:** Stubs return small JSON.
- **Expected Result:** Scraping `GET /metrics` yields (with the default prefix `langgraph_runnable_server`):
  - `langgraph_runnable_server_requests_total{runnable="agent1",endpoint="invoke"} == 1.0`
  - `langgraph_runnable_server_requests_total{runnable="agent1",endpoint="batch"} == 1.0`
  - `langgraph_runnable_server_requests_total{runnable="agent2",endpoint="invoke"} == 1.0`
  - `langgraph_runnable_server_request_duration_seconds_count{runnable="agent1",endpoint="invoke"} == 1`
  - `langgraph_runnable_server_errors_total{runnable=...}` has **no** series (no errors).
  - No series for `agent2,endpoint="batch"` (never called).
- **Failure Mode Covered:** Missing label dimensions, off-by-one increments, global unlabeled metrics, errors counted on a 200 response.

**VC-108: Request/response size histograms**

- **Scenario:** Known payload sizes are reflected in the size histograms.
- **Input/State:** Fixed JSON request body of `N` bytes; fixed JSON response of `M` bytes (use `len(body.encode("utf-8"))`).
- **Expected Result:**
  - `langgraph_runnable_server_request_size_bytes_sum{runnable=...,endpoint=...} == N` (single request).
  - `langgraph_runnable_server_response_size_bytes_sum{runnable=...,endpoint=...} == M` (single response).
  - Both histograms have `_count == 1` for the labeled series.
- **Failure Mode Covered:** Zero sizes always, off-by-one on byte counting, mixing request and response sizes.

**VC-109: Wide logging one event per request**

- **Scenario:** Mixed `GET /health`, `GET /metrics`, `POST .../invoke`, `POST .../batch`, and one validation failure.
- **Input/State:** Structlog test processor list capturing events; one `POST` with body `{}` to trigger 422.
- **Expected Result:** Exactly one INFO event per HTTP request (5 events for 5 requests). Each event Must contain: `http.method`, `http.route`, `http.status_code`, `duration_ms` (float), `instance_id` (matches `app.state["instance_id"]`), **`response_size_bytes`** (integer — **always** present per **BR-301**). **`request_size_bytes`:** Must be present as a non-negative integer when **BR-203** does not require omitting the request-size sample; Must be **omitted** (key absent, not `null`) when **BR-203** requires omit. For the **Input/State** above (`TestClient` requests with well-formed bodies and lengths), all five events **Must** include `request_size_bytes`. Runnable events additionally contain `runnable` and `endpoint`. Probe events Must Not contain `runnable` / `endpoint` keys (omitted, not `null`).
- **Failure Mode Covered:** Zero logs, double logs, missing `instance_id`, `runnable`/`endpoint` leaking into probe events, wrong `duration_ms` type, fabricating `request_size_bytes` when **BR-203** requires omit, omitting `response_size_bytes`, or emitting `null` for omitted optional fields.

**VC-110: Uvicorn access_log disabled does not lose visibility**

- **Scenario:** In-process (no subprocess): verify that the library does **not** install any stdlib `logging` access handler that would duplicate per-request lines when uvicorn's `access_log=False`.
- **Input/State:** `app = create_runnable_app(...)`; install a `caplog`-style stdlib logging capture for the `uvicorn.access` and `fastapi` loggers; install a structlog capture. Issue 5 mixed requests via `TestClient`.
- **Expected Result:** Structlog capture has exactly 5 events. The stdlib `uvicorn.access` and `fastapi` logger captures contain **zero** access-formatted records added by the library (records produced by the test harness itself are excluded by logger-name filter).
- **Failure Mode Covered:** Silent server when access log off, library adding its own stdlib access handler that double-logs.

**VC-111: Lifespan passthrough**

- **Scenario:** Host supplies lifespan toggling `app.state` flags.
- **Input/State:** Same pattern as VC-003b in spec 01 but via `create_runnable_app(prefix="/agents", runnables={}, lifespan=lifespan)`.
- **Expected Result:** Startup runs (state flag observable inside `TestClient` context); shutdown runs (state flag observable after context exit). The library Must Not wrap, compose, or replace the host lifespan.
- **Failure Mode Covered:** Lifespan not forwarded to `create_app`; library wraps the host lifespan with its own.

**VC-112: `__all__` exports**

- **Scenario:** Import package.
- **Input/State:** `import langgraph_runnable_server as m`.
- **Expected Result:** `set(m.__all__) == {"create_app", "create_runnable_app"}` (exactly, no extras).
- **Failure Mode Covered:** Missing export, extra exports.

**VC-113: Empty `runnables` produces a working app (FR-106)**

- **Scenario:** Factory called with an empty `runnables` dict.
- **Input/State:** `app = create_runnable_app(prefix="/agents", runnables={})`.
- **Expected Result:** `GET /health` returns 200 `ok` (spec 01 contract intact); `GET /metrics` returns 200 with Prometheus text and a valid (possibly zero-sample) body; no `POST /agents/.../invoke` route is registered (any such POST returns 404).
- **Failure Mode Covered:** Factory crashes on empty dict; `/metrics` body becomes invalid Prometheus text when no series exist.

**VC-114: Path collision rejected (FR-108)**

- **Scenario:** Host configures runnable and probe prefixes whose normalized full paths collide.
- **Input/State:** At minimum: `create_runnable_app(prefix="/health", runnables={"x": stub}, create_app_prefix="/")` (would produce `/health/x/invoke`, but more importantly registers a router under `/health` overlapping the probe path); and a constructed case where `{runnables_base}/{key}/invoke` equals exactly `{probe_base}/health` or `{probe_base}/metrics` after normalization.
- **Expected Result:** `ValueError` raised at factory time, message naming the colliding path.
- **Failure Mode Covered:** Probe path shadowed by runnable route; silent acceptance of overlapping URLs.

**VC-115: Scraping `/metrics` does not increment invoke/batch metrics (BR-106)**

- **Scenario:** Scrape traffic must not pollute SLO metrics.
- **Input/State:** Issue one `POST /agents/a/invoke`, then issue 5 consecutive `GET /metrics` scrapes.
- **Expected Result:** After the 5 scrapes, `langgraph_runnable_server_requests_total{runnable="a",endpoint="invoke"}` still equals `1.0` (unchanged from the single POST). No new series appear for `runnable="metrics"` or similar.
- **Failure Mode Covered:** Middleware accidentally instruments the `/metrics` path.

**VC-116: Duration histogram exposes BR-202 buckets**

- **Scenario:** Histogram bucket labels match the BR-202 specification exactly.
- **Input/State:** Issue one `POST .../invoke`; scrape `/metrics`; parse `langgraph_runnable_server_request_duration_seconds_bucket` series.
- **Expected Result:** The set of `le=` labels equals `{"0.005", "0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1.0", "2.5", "5.0", "10.0", "+Inf"}` (Prometheus exposition formats integers like `1` as `1.0` and `+Inf` literal). No extra or missing buckets.
- **Failure Mode Covered:** Using `prometheus_client` defaults instead of the BR-202 buckets; missing `+Inf`.

**VC-117: Per-app registry isolation (FR-122)**

- **Scenario:** Two `create_runnable_app` calls in one process must not collide on registry registration.
- **Input/State:** `app1 = create_runnable_app(prefix="/agents", runnables={"a": stub1})`; `app2 = create_runnable_app(prefix="/agents", runnables={"a": stub2})` in the same interpreter; issue one `POST /agents/a/invoke` against each via separate `TestClient`s.
- **Expected Result:** Both factory calls succeed (no `Duplicated timeseries` error). `app1.state["metrics_registry"] is not app2.state["metrics_registry"]`. Scraping `/metrics` on `app1` shows `langgraph_runnable_server_requests_total == 1` for the `a/invoke` series; scraping `/metrics` on `app2` shows independently `langgraph_runnable_server_requests_total == 1`. Neither registry shows the other's samples.
- **Failure Mode Covered:** Use of `prometheus_client.REGISTRY` default global; cross-app metric pollution.

**VC-121: Configurable `metrics_namespace` (FR-123)**

- **Scenario:** The metric name prefix is driven by the `metrics_namespace` argument, with the documented default and validation behavior.
- **Input/State:** Build three apps in the same process:
  1. `app_default = create_runnable_app(prefix="/agents", runnables={"a": stub_a})` (default namespace).
  2. `app_custom = create_runnable_app(prefix="/agents", runnables={"a": stub_a}, metrics_namespace="acme_agents")`.
  3. `app_bare = create_runnable_app(prefix="/agents", runnables={"a": stub_a}, metrics_namespace="")`.

  Issue one `POST /agents/a/invoke` against each via its own `TestClient`, then scrape `/metrics`.
- **Expected Result:**
  - **Default:** scrape contains the literal line family `langgraph_runnable_server_requests_total{runnable="a",endpoint="invoke"} 1.0`; `app_default.state["metrics_namespace"] == "langgraph_runnable_server"`.
  - **Custom:** scrape contains `acme_agents_requests_total{runnable="a",endpoint="invoke"} 1.0` and **no** series whose name starts with `langgraph_runnable_server_`; `app_custom.state["metrics_namespace"] == "acme_agents"`.
  - **Bare:** scrape contains `requests_total{runnable="a",endpoint="invoke"} 1.0` (no prefix) and **no** series whose name starts with `langgraph_runnable_server_` or `acme_agents_`; `app_bare.state["metrics_namespace"] == ""`.
  - **Validation rejections:** Each of the following calls Must raise the indicated exception before any `FastAPI` instance is returned:
    - `metrics_namespace=123` → `TypeError`.
    - `metrics_namespace="1bad"` (starts with digit) → `ValueError`.
    - `metrics_namespace="bad-name"` (contains `-`) → `ValueError`.
    - `metrics_namespace="bad:name"` (contains `:` — reserved for recording rules) → `ValueError`.
    - `metrics_namespace="bad name"` (contains whitespace) → `ValueError`.
- **Failure Mode Covered:** Hard-coded prefix in metric registration; missing validation allowing invalid Prometheus metric names; default value drift; missing `app.state["metrics_namespace"]` for observability.

**VC-118: Runnable exception body shape and traceback discipline (FR-109)**

- **Scenario:** Verify the 500 response uses `detail` and does not leak a stack trace to the client.
- **Input/State:** Stub `ainvoke` raises `RuntimeError("boom")`; issue `POST .../invoke`.
- **Expected Result:** Status 500; response body is JSON with key `detail` (string); body Must Not contain substrings `"Traceback"`, `"File \""`, or the names of any internal modules. The structlog capture for this request, however, Must contain the exception type and traceback in a documented field (e.g. `exc_info` or `error.stack`).
- **Failure Mode Covered:** Stack traces leaked to clients; missing traceback in logs (operators have no signal).

**VC-119: Edge-case request bodies (BR-102, BR-104, BR-109)**

- **Scenario:** A battery of body-validation edge cases.
- **Input/State:** Against a registered runnable:
  - `POST .../invoke` with body `{"input": null}` → expect 200 (null is a valid input).
  - `POST .../batch` with body `{"inputs": []}` → expect 200 with body `[]` and **no** call to `abatch` (stub records zero invocations).
  - `POST .../invoke` with body `[1, 2, 3]` (root is an array) → expect 422.
  - `POST .../invoke` with body `42` (root is a number) → expect 422.
  - `POST .../invoke` with raw bytes and no `Content-Type` header → expect 422.
  - `POST .../invoke` with body `{"input": 1}` and `Content-Type: text/plain` → expect 422.
- **Expected Result:** As above; each non-2xx response increments `errors_total{http_status_class="4xx",...}`.
- **Failure Mode Covered:** Permissive body parsing; calling `abatch` with an empty list; ignoring `Content-Type`.

**VC-120: End-to-end acceptance (full surface)**

- **Scenario:** A single pytest test that exercises the entire `create_runnable_app` public surface in one run. When this VC is green, the library is acceptable for a host to depend on.
- **Input/State:**

  ```python
  from fastapi.testclient import TestClient
  from langgraph_runnable_server import create_app, create_runnable_app

  class StubRunnable:
      def __init__(self):
          self.ainvoke_calls = []
          self.abatch_calls = []
      async def ainvoke(self, input, config=None):
          self.ainvoke_calls.append((input, config))
          return {"echo": input}
      async def abatch(self, inputs, config=None):
          self.abatch_calls.append((inputs, config))
          return [{"echo": i} for i in inputs]

  r1, r2 = StubRunnable(), StubRunnable()
  app = create_runnable_app(
      prefix="/agents",
      runnables={"agent1": r1, "agent2": r2},
      create_app_prefix="/",
  )
  ```

- **Expected Result (all Must hold in a single test):**
  1. **Probes intact** (spec 01 contracts): `GET /health` → 200 `b"ok"`; `GET /metrics` → 200 Prometheus text with `Content-Type: text/plain; version=0.0.4; charset=utf-8`.
  2. **Routing isolation** (FR-102, FR-104): `POST /agents/agent1/invoke {"input": {"x": 1}}` → 200, body equals `{"echo": {"x": 1}}`; only `r1.ainvoke_calls` has length 1, `r2.ainvoke_calls` is empty.
  3. **Batch works** (FR-103, BR-102): `POST /agents/agent2/batch {"inputs": [{"x": 1},{"x": 2}]}` → 200 with body `[{"echo": {"x": 1}},{"echo": {"x": 2}}]`; `r2.abatch_calls[0][0] == [{"x": 1},{"x": 2}]`.
  4. **Method discipline** (BR-105): `GET /agents/agent1/invoke` → 405; `POST /agents/no_such_key/invoke` → 404.
  5. **Validation error** (BR-104): `POST /agents/agent1/invoke {}` → 422.
  6. **Runnable exception** (FR-109): with a temporary stub that raises, `POST /agents/.../invoke` → 500, body is JSON with `detail` key, no `Traceback` substring.
  7. **Metrics composed correctly** (default `metrics_namespace`): scrape `/metrics`; assert `langgraph_runnable_server_requests_total{runnable="agent1",endpoint="invoke"} == 1`, `langgraph_runnable_server_requests_total{runnable="agent2",endpoint="batch"} == 1`, `langgraph_runnable_server_errors_total` reflects the 422 and the 500, and `langgraph_runnable_server_request_duration_seconds_count` for the happy paths each equal 1.
  8. **Per-app registry and namespace** (FR-122, FR-123): `app.state["metrics_registry"]` exists and is a `CollectorRegistry`; it is not `prometheus_client.REGISTRY`. `app.state["metrics_namespace"] == "langgraph_runnable_server"`.
  9. **Structlog wide events** (FR-130, BR-301): the structlog capture contains one event per HTTP request issued by the test, each with the BR-301 minimum fields; runnable events have `runnable` and `endpoint`, probe events do not.
  10. **`__all__` discipline** (FR-112): `set(__all__) == {"create_app", "create_runnable_app"}`.
- **Failure Mode Covered:** Surface regression in any of FR-102, FR-103, FR-104, FR-105, FR-106, FR-109, FR-110, FR-111, FR-112, FR-120, FR-121, FR-122, FR-123, FR-130, BR-101, BR-102, BR-103, BR-104, BR-105, BR-107, BR-301. Overlaps with per-VC tests intentionally; its purpose is "the surface composes correctly," not granular failure attribution.

## Open Questions

- **OQ-001:** Should runnable keys allow **Unicode** letters beyond **BR-107** for international agent names, at the cost of stricter URL encoding rules?

- **OQ-002:** Should **`abatch`** accept per-item configs (array of configs) as an extension—out of scope for v1 unless a concrete LangGraph version standardizes it?

## Assumptions

- **A-001:** Hosts mount the returned **`FastAPI`** app at ASGI root for URL assertions in tests; sub-mounts at host level rewrite paths — VCs use root mount unless stated.
- **A-002:** **`Runnable`** types come from **`langchain-core`** (the chosen umbrella per NFR-104). Upgrading `langchain-core` may require test stub adjustments without changing this spec's HTTP/metrics/logging contracts.
- **A-003:** **`create_app`-only** behavior and empty metrics from spec 01 remain valid until a future spec explicitly unifies metrics across both factories (see "Amendments to spec 01" for the scope of changes in this spec).
- **A-004:** Hosts run uvicorn with `access_log=False` per NFR-108. VCs that depend on log-volume invariants (VC-109, VC-110, NFR-107) assume this configuration.
- **A-005:** Hosts front the library with a reverse proxy or gateway that enforces request body size limits and timeouts per NFR-111; the library itself does not.

## Changelog

| Version | Date | Notes |
|---------|------|-------|
| 1.0 | 2026-05-11 | Initial draft: runnable invoke/batch, `create_runnable_app`, Prometheus per runnable+endpoint, structlog wide events, access log stance. |
| 1.1 | 2026-05-11 | Resolved review action items. Committed concrete picks for previously-deferred decisions: BR-103 (FastAPI `jsonable_encoder`), BR-102 (forward `config` verbatim, empty `inputs` short-circuits to `[]`), BR-203 (omit sample when length unknown), FR-109 (error key `detail`, no traceback to clients), NFR-104 (`langchain-core` umbrella). Added FR-122 (per-app `CollectorRegistry` at `app.state["metrics_registry"]`). Added "Amendments to spec 01" subsection making the `__all__` extension and `/metrics` replacement explicit. Simplified FR-105 to `dict`-only via `TypeError`. Promoted FR-108 (path collision) to Must. Split FR-110 into FR-110 (signature) / FR-111 (prefix normalization) / FR-112 (lifespan + `create_app_prefix` forwarding + `__all__`). Added FR/BR for edge cases: BR-104 (Content-Type, null input), BR-105 (POST methods → 405), BR-108 (client disconnect / cancellation), BR-109 (body root must be JSON object). Pinned BR-202 buckets exactly. Tightened BR-203 chunked encoding to "omit sample." Pinned BR-301 `trace_id` source to W3C `traceparent`. Added NFR-111 (security boundary — body size, timeouts delegated to host). Pinned `/metrics` Content-Type to `text/plain; version=0.0.4; charset=utf-8`. Tightened existing VCs (VC-106, VC-107, VC-108, VC-109, VC-110, VC-111) with exact post-conditions. Added VC-106b, VC-113 (empty runnables), VC-114 (collision), VC-115 (scrape isolation), VC-116 (histogram buckets), VC-117 (per-app registry isolation), VC-118 (no traceback to client), VC-119 (body edge cases), and VC-120 (end-to-end acceptance test, analog to spec 01 VC-021). |
| 1.2 | 2026-05-11 | **Configurable Prometheus namespace.** Added **`metrics_namespace: str = "langgraph_runnable_server"`** keyword argument to `create_runnable_app` (FR-110 signature). Added **FR-123** specifying validation (`^[a-zA-Z_][a-zA-Z0-9_]*$` for non-empty values, `:` rejected, empty string allowed for "no prefix"), default value (backward-compatible), and `app.state["metrics_namespace"]` storage. Rewrote the Prometheus metric families table to use **`{ns}_`** placeholder; default and example expansions documented. Added **VC-121** covering default / custom / empty namespace cases and validation rejections. Updated VC-107 / VC-115 / VC-117 / VC-120 to reference the default namespace explicitly. |
