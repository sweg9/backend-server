"""Starts the hub parts from settings/hub.yaml: the database, the RAL and the link to the server.

Built so far: the records and the app's calls (app.py), the emergency stop through the RAL, and the
link with its heartbeat. The scan, the triggers and the runs join here as they are built.
"""

import asyncio
import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from backend.common.messages import HubStatus, RalStatus, StopDone
from backend.common.settings import REPO, HubSettings
from backend.hub.app import AppHandlers
from backend.hub.db.session import make_engine, make_sessionmaker
from backend.hub.link import HubLink
from backend.hub.ral import make_ral

log = logging.getLogger(__name__)


def migrate(database_url: str) -> None:
    """Brings the database to the newest migration; creates the file and its folder if needed."""
    if database_url.startswith("sqlite:///") and not database_url.endswith(":memory:"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    cfg = Config(str(REPO / "alembic.ini"))
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url  # migrations/env.py reads it
    try:
        command.upgrade(cfg, "head")
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old


def build(s: HubSettings, server_url: str, hub_token: str, password_hash: str | None) -> HubLink:
    db = make_sessionmaker(make_engine(s.database_url))
    ral = make_ral(s.hand)
    state = {"ral": "idle"}

    async def on_stop() -> StopDone:
        try:
            await ral.stop()
            state["ral"] = "stopped"
            return StopDone(ral="stopped")
        except Exception:
            log.exception("the RAL did not stop")
            state["ral"] = "error"
            return StopDone(ral="error")

    def status() -> HubStatus:
        # The Reader (camera.py) is not built yet, so the camera reads as down.
        return HubStatus(camera="down", ral=RalStatus(impl=ral.impl, state=state["ral"]), run_id=None, watching=0)

    async def emit(kind: str, data: dict) -> None:
        await link.event(kind, data)

    app = AppHandlers(db, s.session_days, password_hash, emit=emit)
    link = HubLink(server_url, hub_token, app.handle, on_stop, status, heartbeat_s=s.heartbeat_s)
    return link


async def serve(s: HubSettings, server_url: str, hub_token: str, password_hash: str | None) -> None:
    migrate(s.database_url)
    link = build(s, server_url, hub_token, password_hash)
    try:
        await link.run_forever()
    finally:
        await link.close()


def run(s: HubSettings, server_url: str | None = None) -> int:
    token = os.environ.get("HUB_TOKEN")
    password_hash = os.environ.get("APP_PASSWORD_HASH")
    if not token:
        log.error("HUB_TOKEN is not set: the server would refuse the hub")
        return 1
    if not password_hash:
        log.warning("APP_PASSWORD_HASH is not set: nobody can sign in (make one with `backend hash-password`)")
    try:
        asyncio.run(serve(s, server_url or s.server_url, token, password_hash))
    except KeyboardInterrupt:
        pass
    return 0
