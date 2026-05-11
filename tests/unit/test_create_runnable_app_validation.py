"""Unit tests for create_runnable_app factory validation (no HTTP)."""

from __future__ import annotations

import collections
import types
from typing import Any, cast

import pytest
from langchain_core.runnables import Runnable

from langgraph_runnable_server import create_runnable_app
from stub_runnable import StubRunnable


@pytest.fixture
def stub() -> StubRunnable:
    return StubRunnable()


@pytest.mark.parametrize(
    ("bad_key",),
    [
        ("a/b",),
        ("",),
        ("a" * 65,),
        ("agent name",),
        ("agent$1",),
    ],
)
def test_vc106_invalid_keys_raise_value_error(stub: StubRunnable, bad_key: str) -> None:
    """Invalid runnable keys (BR-107) raise ValueError from create_runnable_app."""
    # Given
    runnables = cast(dict[str, Runnable], {bad_key: stub})
    # When / Then
    with pytest.raises(ValueError):
        create_runnable_app(prefix="/agents", runnables=runnables)


@pytest.mark.parametrize("valid_key", ["a", "a" * 64])
def test_vc106_boundary_keys_accepted(stub: StubRunnable, valid_key: str) -> None:
    """Key length 1 or 64 (BR-107) yields a FastAPI instance from create_runnable_app."""
    # Given
    runnables = cast(dict[str, Runnable], {valid_key: stub})
    # When
    app = create_runnable_app(prefix="/agents", runnables=runnables)
    # Then
    assert app.__class__.__name__ == "FastAPI"


def test_vc106b_userdict_raises_type_error(stub: StubRunnable) -> None:
    """UserDict for runnables raises TypeError (FR-105)."""
    # Given
    runnables = cast(Any, collections.UserDict({"a": stub}))
    # When / Then
    with pytest.raises(TypeError, match="runnables must be a dict"):
        create_runnable_app(prefix="/agents", runnables=runnables)


def test_vc106b_mapping_proxy_raises_type_error(stub: StubRunnable) -> None:
    """MappingProxyType for runnables raises TypeError (FR-105)."""
    # Given
    runnables = cast(Any, types.MappingProxyType({"a": stub}))
    # When / Then
    with pytest.raises(TypeError, match="runnables must be a dict"):
        create_runnable_app(prefix="/agents", runnables=runnables)


def test_vc112_all_exports() -> None:
    """Package __all__ lists exactly create_app and create_runnable_app (VC-112)."""
    import langgraph_runnable_server as m

    # When / Then
    assert set(m.__all__) == {"create_app", "create_runnable_app"}


@pytest.mark.parametrize(
    ("prefix", "msg_fragment"),
    [
        ("/health", "/health/x/invoke"),
        ("/metrics", "/metrics/x/invoke"),
    ],
)
def test_vc114_probe_overlap_raises_value_error(
    stub: StubRunnable, prefix: str, msg_fragment: str
) -> None:
    """Runnable prefix overlapping probes raises ValueError naming a path (FR-108, VC-114)."""
    # When
    with pytest.raises(ValueError) as exc_info:
        create_runnable_app(
            prefix=prefix,
            runnables=cast(dict[str, Runnable], {"x": stub}),
            create_app_prefix="/",
        )
    # Then
    msg = str(exc_info.value)
    assert msg_fragment in msg
    assert "collides" in msg


def test_vc114_health_metrics_keys_overlap_with_root_prefix(stub: StubRunnable) -> None:
    """
    Given runnable base '/' and keys 'health' or 'metrics', runnable paths are /health/invoke etc.

    FR-108 text stresses full-path equality to probes; VC-114 requires overlap rejection for
    prefix='/health'. Here /health/invoke extends /health/ so the stricter overlap rule rejects
    keys named like probe segments at root runnable base.
    """
    # When / Then
    with pytest.raises(ValueError) as exc_info:
        create_runnable_app(
            prefix="/",
            runnables=cast(dict[str, Runnable], {"health": stub, "metrics": stub}),
            create_app_prefix="/",
        )
    assert "collides" in str(exc_info.value)


@pytest.mark.parametrize(
    "invalid_prefix",
    ["agents", "/agents//x", "/agents space"],
)
def test_fr111_invalid_prefix_raises(stub: StubRunnable, invalid_prefix: str) -> None:
    """Invalid runnable prefix (FR-011) raises ValueError from create_runnable_app."""
    # When / Then
    with pytest.raises(ValueError):
        create_runnable_app(
            prefix=invalid_prefix,
            runnables=cast(dict[str, Runnable], {"k": stub}),
        )


@pytest.mark.parametrize("valid_prefix", ["/", "/agents", "/agents/"])
def test_fr111_valid_prefix_normalized(stub: StubRunnable, valid_prefix: str) -> None:
    """Given valid runnable prefixes, when create_runnable_app runs, then the factory succeeds."""
    # When
    app = create_runnable_app(
        prefix=valid_prefix,
        runnables=cast(dict[str, Runnable], {"k": stub}),
    )
    # Then
    assert app.__class__.__name__ == "FastAPI"
