"""Routes of the Safety tag of openapi/backend-server.yaml."""

from fastapi import APIRouter

from backend.cloud.api.common import not_built

router = APIRouter(tags=["Safety"])


@router.post("/stop")
def stop():
    """FR-10"""
    raise not_built("POST /stop")


@router.put("/proactive")
def set_proactive():
    """FR-06"""
    raise not_built("PUT /proactive")
