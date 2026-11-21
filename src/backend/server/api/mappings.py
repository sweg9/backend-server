"""Routes of the Mappings tag of openapi/backend-server.yaml."""

from fastapi import APIRouter, Request

from backend.server.api.common import relay

router = APIRouter(tags=["Mappings"])


@router.get("/mappings")
async def list_mappings(request: Request):
    """FR-02. Relayed to the hub."""
    return await relay(request)


@router.post("/mappings/{mapping_id}/confirm")
async def confirm_mapping(request: Request, mapping_id: str):
    """FR-02. Relayed to the hub."""
    return await relay(request)


@router.post("/mappings/{mapping_id}/reject")
async def reject_mapping(request: Request, mapping_id: str):
    """FR-02. Relayed to the hub."""
    return await relay(request)
