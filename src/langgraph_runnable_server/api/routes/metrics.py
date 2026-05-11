"""Metrics scrape route (GET /metrics)."""

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()


@router.get("/metrics")
def metrics(request: Request) -> Response:
    registry = getattr(request.app.state, "metrics_registry", None)
    if registry is None:
        return Response(content=b"", status_code=200)
    body = generate_latest(registry)
    return Response(content=body, media_type=CONTENT_TYPE_LATEST, status_code=200)
