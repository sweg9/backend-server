"""Routes of the Controls tag of openapi/backend-server.yaml."""

from fastapi import APIRouter, Request

from backend.server.api.common import relay

router = APIRouter(tags=["Controls"])


@router.get("/controls")
async def list_controls(request: Request):
    """FR-01. Relayed to the hub."""
    return await relay(request)
