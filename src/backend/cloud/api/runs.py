"""Routes of the Runs tag of openapi/backend-server.yaml."""

from fastapi import APIRouter

from backend.cloud.api.common import not_built

router = APIRouter(tags=["Runs"])


@router.get("/runs")
def list_runs():
    """FR-12"""
    raise not_built("GET /runs")
