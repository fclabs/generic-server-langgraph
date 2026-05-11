"""Metrics scrape route (GET /metrics)."""

from fastapi import APIRouter, Response

router = APIRouter()


@router.get("/metrics")
def metrics() -> Response:
    return Response(content=b"", status_code=200)
