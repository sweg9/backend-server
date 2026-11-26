"""Routes of the Situations and Robot tags of openapi/backend-server.yaml. All relayed to the hub."""

from fastapi import APIRouter, Request

from backend.server.api.common import relay

router = APIRouter()


@router.get("/situations", tags=["Situations"])
async def list_situations(request: Request):
    """FR-05, FR-06."""
    return await relay(request)


@router.get("/situations/{situation_id}", tags=["Situations"])
async def get_situation(request: Request, situation_id: str):
    """FR-05, FR-06."""
    return await relay(request)


@router.post("/situations/{situation_id}/approve", tags=["Situations"])
async def approve_situation(request: Request, situation_id: str):
    """FR-06."""
    return await relay(request)


@router.post("/situations/{situation_id}/dismiss", tags=["Situations"])
async def dismiss_situation(request: Request, situation_id: str):
    """FR-06."""
    return await relay(request)


@router.get("/visits", tags=["Situations"])
async def list_visits(request: Request):
    """FR-07, FR-09."""
    return await relay(request)


@router.get("/robot", tags=["Robot"])
async def get_robot(request: Request):
    """FR-09, FR-10."""
    return await relay(request)


@router.get("/map", tags=["Robot"])
async def get_map(request: Request):
    """FR-09. The home as the Dashboard draws it."""
    return await relay(request)


@router.post("/robot/route", tags=["Robot"])
async def route_robot(request: Request):
    """FR-09, FR-10. A move order; the hub passes the safety gate before every leg (C-04)."""
    return await relay(request)
