"""Routes of the Routines tag of openapi/backend-server.yaml."""

from fastapi import APIRouter

from backend.cloud.api.common import not_built

router = APIRouter(tags=["Routines"])


@router.get("/routines")
def list_routines():
    """FR-04"""
    raise not_built("GET /routines")


@router.post("/routines")
def create_routine():
    """FR-04"""
    raise not_built("POST /routines")


@router.post("/routines/{routine_id}/feedback")
def routine_feedback(routine_id: str):
    """FR-04, FR-06"""
    raise not_built("POST /routines/{routine_id}/feedback")
