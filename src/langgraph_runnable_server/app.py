"""Application factory (stub for iteration 1)."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.types import Lifespan


def create_app(
    prefix: str = "/",
    lifespan: Lifespan[FastAPI] | None = None,
) -> FastAPI:
    del prefix, lifespan  # Wired in later iterations
    return FastAPI()
