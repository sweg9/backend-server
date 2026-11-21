"""The server app on AWS: FastAPI in one pod, keeping no records (wiki: Modules > server)."""

import logging

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from backend.common.messages import AppRequest, Envelope
from backend.common.settings import ServerSettings, load
from backend.server.api import ROUTERS
from backend.server.auth import SESSION_COOKIE, LoginLimiter, hub_token_ok
from backend.server.enclave_link import VsockEnclave
from backend.server.hub_gateway import Enclave, HubGateway, HubOffline
from backend.server.live import Live

log = logging.getLogger(__name__)


def create_app(settings: ServerSettings | None = None, enclave: Enclave | None = None) -> FastAPI:
    s = settings or load(ServerSettings, "server.yaml")
    app = FastAPI(title="backend-server", version="0.2.0")
    live = Live()
    app.state.live = live
    app.state.hub = HubGateway(
        enclave or VsockEnclave(s.enclave_cid, s.enclave_port), live, s.app_timeout_s, s.stop_timeout_s
    )
    app.state.login_limiter = LoginLimiter(s.login_max_failures, s.login_window_s)
    for r in ROUTERS:
        app.include_router(r)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        """For the k8s readiness and liveness probes. Not part of the API spec."""
        return {"ok": True, "hub": app.state.hub.online}

    @app.exception_handler(HTTPException)
    async def _error_shape(_req: Request, exc: HTTPException):
        body = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(body, status_code=exc.status_code)

    @app.websocket("/ws/hub")
    async def ws_hub(ws: WebSocket):
        """The hub's one connection: HUB_TOKEN, then requests and answers (wiki: Hub messages)."""
        gateway: HubGateway = app.state.hub
        if not hub_token_ok(ws.headers.get("authorization")):
            await ws.close(code=1008)
            return
        await ws.accept()

        async def send(msg: Envelope) -> None:
            await ws.send_text(msg.model_dump_json())

        gateway.attach(send)
        try:
            while True:
                msg = Envelope.model_validate_json(await ws.receive_text())
                answer = await gateway.handle(msg)
                if answer is not None:
                    await send(answer)
        except WebSocketDisconnect:
            log.info("the hub disconnected")
        finally:
            gateway.detach()

    @app.websocket("/ws/app")
    async def ws_app(ws: WebSocket):
        """Live updates for a signed-in app; the hub checks the session."""
        gateway: HubGateway = app.state.hub
        req = AppRequest(method="GET", path="/me", session=ws.cookies.get(SESSION_COOKIE))
        try:
            ok = (await gateway.app_request(req)).status == 200
        except HubOffline:
            ok = False
        if not ok:
            await ws.close(code=1008)
            return
        await ws.accept()
        live.screens.add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            live.screens.discard(ws)

    return app
