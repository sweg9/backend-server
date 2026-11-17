"""The cloud app: FastAPI with one worker (wiki: Modules > cloud)."""

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from backend.cloud.api import ROUTERS


def create_app() -> FastAPI:
    app = FastAPI(title="backend-server", version="0.1.0")
    for r in ROUTERS:
        app.include_router(r)

    @app.exception_handler(HTTPException)
    async def _error_shape(_req: Request, exc: HTTPException):
        body = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(body, status_code=exc.status_code)

    @app.websocket("/ws/hub")
    async def ws_hub(ws: WebSocket):
        """hub_gateway.py: HUB_TOKEN check, ids already handled, ack after commit. Not built yet."""
        await ws.close(code=1013)

    @app.websocket("/ws/app")
    async def ws_app(ws: WebSocket):
        """live.py: pushes to the signed-in app. Not built yet."""
        await ws.close(code=1013)

    return app
