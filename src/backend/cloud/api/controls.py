"""Routes of the Controls tag of openapi/backend-server.yaml."""

from fastapi import APIRouter

from backend.cloud.api.common import not_built

router = APIRouter(tags=["Controls"])


@router.get("/controls")
def list_controls():
    """FR-01"""
    raise not_built("GET /controls")
