"""Shared pieces of the routers: the one error shape of the API conventions, and the relay to the hub.

The server keeps no records, so every route hands the app's call to the hub as `app.request` and
returns the hub's answer (wiki: Hub messages > app.request). While the hub is offline: 503.
"""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from backend.common.messages import AppRequest
from backend.server.auth import SESSION_COOKIE
from backend.server.hub_gateway import HubGateway, HubOffline


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, reason: str | None = None):
        super().__init__(status, {"code": code, "message": message, "reason": reason})


def hub_offline(e: HubOffline) -> ApiError:
    return ApiError(503, "hub_offline", "The hub at home is offline", str(e))


async def relay(request: Request) -> JSONResponse:
    gateway: HubGateway = request.app.state.hub
    raw = await request.body()
    req = AppRequest(
        method=request.method,
        path=request.url.path,
        query=dict(request.query_params),
        body=await request.json() if raw else None,
        session=request.cookies.get(SESSION_COOKIE),
    )
    try:
        resp = await gateway.app_request(req)
    except HubOffline as e:
        raise hub_offline(e) from e
    out = JSONResponse(resp.body, status_code=resp.status)
    if resp.set_session == "":
        out.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict")
    elif resp.set_session is not None:
        out.set_cookie(SESSION_COOKIE, resp.set_session, secure=True, httponly=True, samesite="strict")
    return out
