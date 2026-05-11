"""Probe-only FastAPI factory: runnable options validated before any ``FastAPI`` exists.

Public API::

    def create_runnable_app(
        *,
        prefix: str,
        runnables: dict[str, Runnable],
        create_app_prefix: str = "/",
        lifespan: Lifespan[FastAPI] | None = None,
        metrics_namespace: str = "langgraph_runnable_server",
    ) -> FastAPI: ...

Validation order:

1. ``runnables`` must be a plain ``dict`` (else ``TypeError``; ``UserDict`` /
   ``MappingProxyType`` rejected).
2. ``metrics_namespace`` must be ``str`` (else ``TypeError``); empty allowed; non-empty must match
   ``^[a-zA-Z_][a-zA-Z0-9_]*$`` (else ``ValueError``).
3. Every key must match ``^[A-Za-z0-9._-]{1,64}$`` (``ValueError``).
4. Runnable ``prefix``: same normalization as ``create_app`` / FR-011 (``_normalize_prefix``).
5. Path collision (FR-108): each ``{runnables_base}/{key}/invoke|batch`` vs
   ``{probe_base}/health`` and ``{probe_base}/metrics`` using **strict overlap** (not equality
   only): equal paths; runnable under a probe prefix; or probe under a runnable prefix
   (shadowing). Covers VC-114 (e.g. ``prefix="/health"``, key ``x``) despite FR-108 "full path"
   wording.

Runnable HTTP routes are **not** registered yet — iter 2 adds ``POST …/invoke`` and
``POST …/batch``.
"""

from __future__ import annotations

import re

from fastapi import FastAPI
from langchain_core.runnables import Runnable
from starlette.types import Lifespan

from ._prefix import _normalize_prefix
from .app import create_app

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_METRICS_NAMESPACE_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _probe_paths(probe_base: str) -> tuple[str, str]:
    if probe_base:
        return f"{probe_base}/health", f"{probe_base}/metrics"
    return "/health", "/metrics"


def _runnable_full_paths(runnables_base: str, key: str) -> tuple[str, str]:
    if runnables_base:
        stem = f"{runnables_base}/{key}"
    else:
        stem = f"/{key}"
    return f"{stem}/invoke", f"{stem}/batch"


def _paths_collide(a: str, b: str) -> bool:
    if a == b:
        return True
    if a.startswith(b + "/"):
        return True
    if b.startswith(a + "/"):
        return True
    return False


def create_runnable_app(
    *,
    prefix: str,
    runnables: dict[str, Runnable],
    create_app_prefix: str = "/",
    lifespan: Lifespan[FastAPI] | None = None,
    metrics_namespace: str = "langgraph_runnable_server",
) -> FastAPI:
    if not isinstance(runnables, dict):
        raise TypeError("runnables must be a dict")

    if not isinstance(metrics_namespace, str):
        raise TypeError("metrics_namespace must be str")
    if metrics_namespace and not _METRICS_NAMESPACE_PATTERN.fullmatch(metrics_namespace):
        raise ValueError("metrics_namespace must match ^[a-zA-Z_][a-zA-Z0-9_]*$ when non-empty")

    for key in runnables:
        if not _KEY_PATTERN.fullmatch(key):
            raise ValueError(
                f"runnable key {key!r} must match ^[A-Za-z0-9._-]{{1,64}}$ (FR-107, BR-107)"
            )

    runnables_base = _normalize_prefix(prefix)
    probe_base = _normalize_prefix(create_app_prefix)
    health_path, metrics_path = _probe_paths(probe_base)

    for key in runnables:
        for path in _runnable_full_paths(runnables_base, key):
            for probe in (health_path, metrics_path):
                if _paths_collide(path, probe):
                    raise ValueError(
                        f"runnable path {path!r} collides with probe path {probe!r} (FR-108)"
                    )

    app = create_app(prefix=create_app_prefix, lifespan=lifespan)
    app.state["metrics_namespace"] = metrics_namespace
    return app
