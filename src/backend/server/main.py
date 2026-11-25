"""The server app on AWS: FastAPI in one pod, keeping no records (wiki: Modules > server)."""

import asyncio
import logging
from pathlib import Path

import yaml
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from backend.common.messages import ENCLAVE_REQUESTS, AppRequest, Envelope
from backend.common.settings import SETTINGS_DIR, ServerSettings, load
from backend.server.api import ROUTERS
from backend.server.auth import SESSION_COOKIE, LoginLimiter, hub_token_ok
from backend.server.credentials import InstanceRole, NoCredentials
from backend.server.enclave_link import EnclaveLink, tcp, vsock
from backend.server.hub_gateway import Enclave, HubGateway, HubOffline
from backend.server.live import Live

log = logging.getLogger(__name__)


def enclave_link(s: ServerSettings, settings_dir: Path = SETTINGS_DIR) -> EnclaveLink:
    """The link from server.yaml. The files for `init` are read again on every new connection."""
    if s.enclave_transport == "tcp":
        if s.enclave_host is None:
            raise ValueError("enclave_transport tcp needs enclave_host")
        connect = tcp(s.enclave_host, s.enclave_port)
    else:
        connect = vsock(s.enclave_cid, s.enclave_port)

    def read(name: str) -> dict:
        with open(settings_dir / name) as f:  # an absolute name stays as it is
            return yaml.safe_load(f)

    def settings() -> tuple[dict, dict, dict]:
        return read("risk.yaml"), read("enclave.yaml"), read(s.engine_settings_file)

    creds = InstanceRole(s.credentials_refresh_before_s) if s.aws_credentials == "instance_role" else NoCredentials()
    return EnclaveLink(connect, settings, creds, s.enclave_timeout_s, s.enclave_init_timeout_s)


def create_app(settings: ServerSettings | None = None, enclave: Enclave | None = None) -> FastAPI:
    s = settings or load(ServerSettings, "server.yaml")
    app = FastAPI(title="backend-server", version="0.2.0")
    live = Live()
    app.state.live = live
    app.state.hub = HubGateway(enclave or enclave_link(s), live, s.app_timeout_s, s.stop_timeout_s)
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

        async def handle(msg: Envelope) -> None:
            answer = await gateway.handle(msg)
            if answer is not None:
                try:
                    await send(answer)
                except Exception:  # the hub left meanwhile; its time limit handles it
                    log.info("could not send %s to the hub", answer.type)

        # Enclave requests take seconds; they run beside the loop, so `stop.done` and the other
        # answers from the hub are read meanwhile (C-04).
        working: set[asyncio.Task] = set()
        gateway.attach(send)
        try:
            while True:
                msg = Envelope.model_validate_json(await ws.receive_text())
                if msg.type in ENCLAVE_REQUESTS:
                    task = asyncio.create_task(handle(msg))
                    working.add(task)
                    task.add_done_callback(working.discard)
                else:
                    await handle(msg)
        except WebSocketDisconnect:
            log.info("the hub disconnected")
        finally:
            gateway.detach()
            for task in working:
                task.cancel()

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
