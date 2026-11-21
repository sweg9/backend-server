"""Routes of the Routines tag of openapi/backend-server.yaml."""

from fastapi import APIRouter, Request

from backend.server.api.common import relay

router = APIRouter(tags=["Routines"])


@router.get("/routines")
async def list_routines(request: Request):
    """FR-04. Relayed to the hub."""
    return await relay(request)


@router.post("/routines")
async def create_routine(request: Request):
    """FR-04. Relayed to the hub."""
    return await relay(request)


@router.post("/routines/{routine_id}/feedback")
async def routine_feedback(request: Request, routine_id: str):
    """FR-04, FR-06. Relayed to the hub."""
    return await relay(request)
