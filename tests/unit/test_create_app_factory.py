"""Unit tests for create_app factory: state, prefix validation, metrics registry (no HTTP)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from langgraph_runnable_server import create_app
from langgraph_runnable_server.metrics import registry


def test_vc001_instance_id_present() -> None:
    """Given a new app from the factory, when reading state, then instance_id is a UUID-shaped string."""
    # Given
    # When
    app = create_app()
    # Then
    instance_id = app.state["instance_id"]
    assert isinstance(instance_id, str)
    assert len(instance_id) > 0
    assert len(instance_id) == 36
    assert instance_id.count("-") == 4
    assert app.state.instance_id == instance_id


def test_vc002_instance_ids_unique_per_app() -> None:
    """Given two apps from the factory, when comparing state, then instance_ids differ."""
    # Given
    # When
    a = create_app()
    b = create_app()
    # Then
    assert a.state["instance_id"] != b.state["instance_id"]


def test_vc010_metrics_registry_empty() -> None:
    """Given the metrics registry module, when reading METRICS, then the tuple is empty for base app."""
    # Given / When / Then
    assert registry.METRICS == ()


@pytest.mark.parametrize("bad_prefix", ["/api///", "/api//"])
def test_vc018_double_slash_in_prefix_rejected_before_normalization(bad_prefix: str) -> None:
    """Given a prefix containing //, when create_app runs, then ValueError and FastAPI is not built."""
    # Given
    with patch("langgraph_runnable_server.app.FastAPI") as mock_fastapi:
        # When / Then
        with pytest.raises(ValueError):
            create_app(prefix=bad_prefix)
        mock_fastapi.assert_not_called()


@pytest.mark.parametrize(
    "bad_prefix",
    [
        "api",
        "//",
        "/api//v1",
        "/a b",
        "/a?b",
        "/a#b",
        "/a<b",
    ],
)
def test_vc019_invalid_prefix_raises_value_error_before_fastapi(bad_prefix: str) -> None:
    """Given invalid prefixes, when create_app runs, then ValueError and FastAPI is never built."""
    # Given
    with patch("langgraph_runnable_server.app.FastAPI") as mock_fastapi:
        # When / Then
        with pytest.raises(ValueError):
            create_app(prefix=bad_prefix)
        mock_fastapi.assert_not_called()
