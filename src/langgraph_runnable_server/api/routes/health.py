"""GET ``/health`` probe (spec 01)."""

from fastapi import APIRouter, Response

router = APIRouter()


@router.get("/health")
def health() -> Response:
    return Response(content=b"ok", media_type="text/plain")
