"""FastAPI factory for health/metrics under a configurable prefix. Public API: `create_app`."""

from .app import create_app

__all__ = ["create_app"]
