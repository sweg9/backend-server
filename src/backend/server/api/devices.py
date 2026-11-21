"""Routes of the Devices tag of openapi/backend-server.yaml."""

from fastapi import APIRouter, Request

from backend.server.api.common import relay

router = APIRouter(tags=["Devices"])


@router.get("/devices")
async def list_devices(request: Request):
    """FR-02. Relayed to the hub."""
    return await relay(request)


@router.patch("/devices/{device_id}")
async def update_device(request: Request, device_id: str):
    """FR-02, FR-06. Relayed to the hub."""
    return await relay(request)
