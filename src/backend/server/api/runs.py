"""Routes of the Runs tag of openapi/backend-server.yaml."""

from fastapi import APIRouter, Request

from backend.server.api.common import relay

router = APIRouter(tags=["Runs"])


@router.get("/runs")
async def list_runs(request: Request):
    """FR-12. Relayed to the hub."""
    return await relay(request)
