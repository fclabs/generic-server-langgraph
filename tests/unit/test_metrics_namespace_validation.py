"""metrics_namespace validation for create_runnable_app (VC-121 slice, iter 1)."""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi import FastAPI
from langchain_core.runnables import Runnable

from langgraph_runnable_server import create_runnable_app
from stub_runnable import StubRunnable


@pytest.fixture
def stub() -> StubRunnable:
    return StubRunnable()


def test_metrics_namespace_int_raises_type_error(stub: StubRunnable) -> None:
    """Given metrics_namespace is not a str, when create_runnable_app runs, then TypeError."""
    # When / Then
    with pytest.raises(TypeError):
        create_runnable_app(
            prefix="/agents",
            runnables=cast(dict[str, Runnable], {"a": stub}),
            metrics_namespace=cast(Any, 123),
        )


@pytest.mark.parametrize(
    "bad_ns",
    ["1bad", "bad-name", "bad:name", "bad name"],
)
def test_metrics_namespace_invalid_regex_raises_value_error(
    stub: StubRunnable, bad_ns: str
) -> None:
    """Invalid non-empty metrics_namespace (FR-123) raises ValueError."""
    # When / Then
    with pytest.raises(ValueError):
        create_runnable_app(
            prefix="/agents",
            runnables=cast(dict[str, Runnable], {"a": stub}),
            metrics_namespace=bad_ns,
        )


def test_metrics_namespace_empty_string_accepted(stub: StubRunnable) -> None:
    """Given metrics_namespace '', when the factory returns, then state stores ''."""
    # When
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"a": stub}),
        metrics_namespace="",
    )
    # Then
    assert app.state["metrics_namespace"] == ""


def test_metrics_namespace_custom_accepted(stub: StubRunnable) -> None:
    """Given a valid custom namespace, when the factory returns, then state matches."""
    # When
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"a": stub}),
        metrics_namespace="acme_agents",
    )
    # Then
    assert app.state["metrics_namespace"] == "acme_agents"


def test_metrics_namespace_default(stub: StubRunnable) -> None:
    """Given metrics_namespace omitted, when the factory returns, then the default is stored."""
    # When
    app = create_runnable_app(
        prefix="/agents",
        runnables=cast(dict[str, Runnable], {"a": stub}),
    )
    # Then
    assert app.state["metrics_namespace"] == "langgraph_runnable_server"
    assert isinstance(app, FastAPI)
