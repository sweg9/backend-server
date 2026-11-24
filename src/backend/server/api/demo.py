"""Routes of the Demo tag of openapi/backend-server.yaml. Relayed; the hub answers 404 unless it runs
with --demo-scenarios."""

from fastapi import APIRouter, Request

from backend.server.api.common import relay

router = APIRouter(tags=["Demo"])


@router.get("/scenarios")
async def list_scenarios(request: Request):
    return await relay(request)


@router.post("/scenarios/{name}/start")
async def start_scenario(request: Request, name: str):
    return await relay(request)
