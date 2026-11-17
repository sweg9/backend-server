"""Routes of the Devices tag of openapi/backend-server.yaml."""

from fastapi import APIRouter

from backend.cloud.api.common import not_built

router = APIRouter(tags=["Devices"])


@router.get("/devices")
def list_devices():
    """FR-02"""
    raise not_built("GET /devices")


@router.patch("/devices/{device_id}")
def update_device(device_id: str):
    """FR-02, FR-06"""
    raise not_built("PATCH /devices/{device_id}")
