"""Routes of the Safety tag of openapi/backend-server.yaml."""

from fastapi import APIRouter, Request

from backend.server.api.common import ApiError, hub_offline, relay
from backend.server.hub_gateway import HubGateway, HubOffline

router = APIRouter(tags=["Safety"])


@router.post("/stop")
async def stop(request: Request):
    """FR-10. The session is checked by the hub first, then `stop` goes down and waits for `stop.done`."""
    me = await relay(request)  # the hub answers POST /stop only with the session check
    if me.status_code != 200:
        return me
    gateway: HubGateway = request.app.state.hub
    try:
        done = await gateway.stop()
    except HubOffline as e:
        raise (
            ApiError(
                504,
                "stop_not_confirmed",
                "The stop was not confirmed. Use the key on the hub or the power switch",
                str(e),
            )
            if gateway.online
            else hub_offline(e)
        ) from e
    return {"ral": done.ral}


@router.get("/proactive")
async def get_proactive(request: Request):
    """FR-06. Relayed to the hub."""
    return await relay(request)


@router.put("/proactive")
async def set_proactive(request: Request):
    """FR-06. Relayed to the hub."""
    return await relay(request)
