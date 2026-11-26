"""Starts the hub parts from settings/hub.yaml: the database, the RAL and the link to the server.

Built so far: the records and the app's calls (app.py), the orchestrator with the emergency stop, the
link with its heartbeat, the real answers over the link with the Reader's photos (answers.py, camera.py),
and the demo scenarios as a trigger source (--demo-scenarios). The scan and the time and sensor triggers
join here as they are built.
"""

import asyncio
import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from backend.common.messages import HubStatus, RalStatus, StopDone
from backend.common.settings import REPO, HubSettings
from backend.common.vocab import Channel
from backend.hub.answers import LinkAnswers
from backend.hub.app import AppHandlers
from backend.hub.camera import Exposure, Reader
from backend.hub.channels.stub import StubAdapter
from backend.hub.db.session import make_engine, make_sessionmaker
from backend.hub.link import HubLink
from backend.hub.orchestrator import Orchestrator
from backend.hub.ral import make_ral
from backend.hub.runs import end_left_open
from backend.hub.seal import data_keys

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


def build(
    s: HubSettings, server_url: str, hub_token: str, password_hash: str | None, demo_scenarios: bool = False
) -> HubLink:
    db = make_sessionmaker(make_engine(s.database_url))
    with db() as session:
        ended = end_left_open(session, s.time_zone)
    if ended:
        log.warning("ended %d runs left open by a restart", ended)
    ral = make_ral(s.hand, s.serial_port, [a.id for a in s.actuators])

    async def emit(kind: str, data: dict) -> None:
        await link.event(kind, data)

    kms: list = []  # one KMS client, made at the first photo; SealError while kms_key_id is not set

    def keys():
        if not kms:
            kms.append(data_keys(s))
        return kms[0]

    exposure = Exposure(
        s.camera_lock_exposure, s.camera_exposure_auto, s.camera_exposure_manual, s.camera_settle_frames
    )
    reader = Reader(
        s.camera_index, s.photo_max_side, s.photo_jpeg_quality, s.camera_flush_frames, keys=keys, exposure=exposure
    )
    if not s.kms_key_id:
        log.warning("kms_key_id is not set: no photo leaves the home, so every camera step goes on the safe side")

    async def ask(type_, payload, timeout_s):
        return await link.ask(type_, payload, timeout_s)

    orch = Orchestrator(
        db,
        ral,
        {c: StubAdapter(c) for c in (Channel.IR, Channel.WIFI, Channel.BT)},
        LinkAnswers(ask, reader.photo, db, s),
        emit,
        time_zone=s.time_zone,
        effect_wait_s=s.effect_wait_s,
        unsure_recheck_s=s.unsure_recheck_s,
        actuator=s.actuators[0].id,
    )

    async def on_stop() -> StopDone:
        return StopDone(ral=await orch.stop())

    def status() -> HubStatus:
        run_id = (orch.robot.task or {}).get("run_id")
        return HubStatus(
            camera="ok" if reader.up else "down",
            ral=RalStatus(impl=ral.impl, state=orch.robot.ral_state),
            run_id=run_id,
            watching=0,
        )

    app = AppHandlers(db, s.session_days, password_hash, emit=emit, orch=orch, demo_scenarios=demo_scenarios)
    link = HubLink(server_url, hub_token, app.handle, on_stop, status, heartbeat_s=s.heartbeat_s)
    return link


async def serve(
    s: HubSettings, server_url: str, hub_token: str, password_hash: str | None, demo_scenarios: bool
) -> None:
    migrate(s.database_url)
    link = build(s, server_url, hub_token, password_hash, demo_scenarios)
    try:
        await link.run_forever()
    finally:
        await link.close()


def run(s: HubSettings, server_url: str | None = None, demo_scenarios: bool = False) -> int:
    token = os.environ.get("HUB_TOKEN")
    password_hash = os.environ.get("APP_PASSWORD_HASH")
    if not token:
        log.error("HUB_TOKEN is not set: the server would refuse the hub")
        return 1
    if not password_hash:
        log.warning("APP_PASSWORD_HASH is not set: nobody can sign in (make one with `backend hash-password`)")
    try:
        if demo_scenarios:
            log.warning("demo scenarios are on: POST /scenarios/{name}/start plays fixed answers")
        asyncio.run(serve(s, server_url or s.server_url, token, password_hash, demo_scenarios))
    except KeyboardInterrupt:
        pass
    return 0
