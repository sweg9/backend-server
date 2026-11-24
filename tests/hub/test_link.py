"""End to end: the real server and the hub's link over a real WebSocket (wiki: Hub messages).

The app's calls go server -> app.request -> hub -> app.response, stop goes down and stop.done comes
back, and the hub's changes reach the app's WebSocket as app.event.
"""

import asyncio
import json
import socket

import httpx
import pytest
import uvicorn
import websockets
from argon2 import PasswordHasher

from backend.common.settings import HubSettings, ServerSettings, load
from backend.hub.db.models import Base
from backend.hub.db.session import make_engine, make_sessionmaker
from backend.hub.main import build
from backend.hub.seed import seed_demo
from backend.server.main import create_app

HASH = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("pw")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_for(cond, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.02)


@pytest.mark.fr("FR-10")
def test_app_calls_events_and_stop_go_through_the_server_to_the_hub(tmp_path, monkeypatch):
    monkeypatch.setenv("HUB_TOKEN", "t0k3n")
    url = f"sqlite:///{tmp_path / 'app.db'}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    with make_sessionmaker(engine)() as db:
        seed_demo(db, "Asia/Seoul")
    hub_settings = load(HubSettings, "hub.yaml").model_copy(update={"database_url": url, "heartbeat_s": 0.2})
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    server_app = create_app(load(ServerSettings, "server.yaml"))

    async def go():
        server = uvicorn.Server(uvicorn.Config(server_app, port=port, log_level="warning"))
        server_task = asyncio.create_task(server.serve())
        await wait_for(lambda: server.started)
        link = build(hub_settings, f"ws://127.0.0.1:{port}/ws/hub", "t0k3n", HASH)
        link_task = asyncio.create_task(link.run_forever())
        try:
            async with httpx.AsyncClient(base_url=base) as http:
                await wait_for(link.connected.is_set)
                # sign in; the cookie is Secure, so it is sent back by hand over plain http
                r = await http.post("/login", json={"password": "pw"})
                assert r.status_code == 204, r.text
                token = r.cookies.get("session") or r.headers["set-cookie"].split(";")[0].split("=", 1)[1]
                cookie = {"Cookie": f"session={token}"}
                assert (await http.get("/routines", headers=cookie)).status_code == 200
                assert (await http.get("/routines")).status_code == 401

                # a change made through the API comes back on the app's WebSocket
                async with websockets.connect(f"ws://127.0.0.1:{port}/ws/app", additional_headers=cookie) as app_ws:
                    stale = (await http.get("/mappings", params={"status": "Stale"}, headers=cookie)).json()[0]
                    r = await http.post(f"/mappings/{stale['mapping_id']}/confirm", headers=cookie)
                    assert r.json()["status"] == "Verified"
                    event = json.loads(await asyncio.wait_for(app_ws.recv(), 5))
                    assert event == {"kind": "mapping", "data": r.json()}

                r = await http.post("/stop", headers=cookie)
                assert r.status_code == 200 and r.json() == {"ral": "stopped"}
                hub = server_app.state.hub
                await wait_for(lambda: hub.status is not None and hub.status.ral.state == "stopped")
        finally:
            link_task.cancel()
            server.should_exit = True
            await server_task

    asyncio.run(go())
