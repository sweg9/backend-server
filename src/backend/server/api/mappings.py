"""Routes of the Mappings tag of openapi/backend-server.yaml."""

from fastapi import APIRouter

from backend.server.api.common import not_built

router = APIRouter(tags=["Mappings"])


@router.get("/mappings")
def list_mappings():
    """FR-02"""
    raise not_built("GET /mappings")


@router.post("/mappings/{mapping_id}/confirm")
def confirm_mapping(mapping_id: str):
    """FR-02"""
    raise not_built("POST /mappings/{mapping_id}/confirm")


@router.post("/mappings/{mapping_id}/reject")
def reject_mapping(mapping_id: str):
    """FR-02"""
    raise not_built("POST /mappings/{mapping_id}/reject")
