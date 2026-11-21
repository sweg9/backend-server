"""Routes of the Session tag of openapi/backend-server.yaml. The hub checks the password and the session."""

from fastapi import APIRouter, Request

from backend.server.api.common import ApiError, relay

router = APIRouter(tags=["Session"])


@router.post("/login")
async def login(request: Request):
    """C-10. Relayed to the hub; the server only limits the tries."""
    limiter = request.app.state.login_limiter
    if limiter.blocked():
        raise ApiError(429, "too_many_tries", "Too many wrong passwords; try again in a minute")
    resp = await relay(request)
    if resp.status_code == 401:
        limiter.failed()
    return resp


@router.post("/logout")
async def logout(request: Request):
    """C-10. Relayed to the hub."""
    return await relay(request)


@router.get("/me")
async def me(request: Request):
    """C-10. Relayed to the hub."""
    return await relay(request)
