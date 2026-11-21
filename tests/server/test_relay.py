"""The server keeps no records: it relays the app to the hub and the hub's requests to the enclave
(wiki: Hub messages)."""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.common.messages import ANSWERS, AppResponse, Envelope, StopDone
from backend.common.settings import ServerSettings, load
from backend.server.hub_gateway import HubGateway, HubOffline, envelope
from backend.server.main import create_app
from tests.conftest import sealed

S = load(ServerSettings, "server.yaml")


class FakeHub:
    """Answers every app.request with what `reply` returns, and every stop with stop.done."""

    def __init__(self, gateway: HubGateway, reply=None, silent=False):
        self.gateway = gateway
        self.reply = reply or (lambda req: AppResponse(status=200, body={"path": req["path"]}))
        self.silent = silent
        self.seen: list[Envelope] = []

    async def send(self, msg: Envelope) -> None:
        self.seen.append(msg)
        if self.silent:
            return
        body = self.reply(msg.payload) if msg.type == "app.request" else StopDone(ral="stopped")
        answer = envelope(ANSWERS[msg.type], body, re=msg.id)
        asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self.gateway.handle(answer)))


class FakeEnclave:
    def __init__(self):
        self.asked: list[Envelope] = []

    async def ask(self, request: Envelope) -> Envelope:
        self.asked.append(request)
        payload = {"decision": "act", "confidence": 0.9, "reason": "low risk and high confidence"}
        return Envelope.model_validate(
            {
                "type": "decide.result",
                "id": "m-e-1",
                "re": request.id,
                "run_id": request.run_id,
                "sent_at": datetime.now(UTC),
                "payload": payload,
            }
        )


def client_with_hub(**kw):
    app = create_app(S)
    hub = FakeHub(app.state.hub, **kw)
    app.state.hub.attach(hub.send)
    return TestClient(app), hub


def test_calls_get_503_while_the_hub_is_offline():
    r = TestClient(create_app(S)).get("/routines")
    assert r.status_code == 503
    assert r.json()["code"] == "hub_offline"


def test_a_call_is_relayed_to_the_hub_with_the_session_cookie():
    client, hub = client_with_hub()
    client.cookies.set("session", "s3cr3t")
    r = client.get("/mappings", params={"status": "Pending"})
    assert r.status_code == 200
    assert r.json() == {"path": "/mappings"}
    req = hub.seen[0].payload
    assert (req["method"], req["query"], req["session"]) == ("GET", {"status": "Pending"}, "s3cr3t")


def test_a_hub_that_does_not_answer_in_time_gives_503():
    app = create_app(S.model_copy(update={"app_timeout_s": 0.05}))
    app.state.hub.attach(FakeHub(app.state.hub, silent=True).send)
    assert TestClient(app).get("/controls").status_code == 503


def test_login_sets_the_cookie_the_hub_made():
    client, _ = client_with_hub(reply=lambda req: AppResponse(status=200, body={}, set_session="abc"))
    r = client.post("/login", json={"password": "x"})
    assert r.status_code == 200
    assert "abc" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]


def test_too_many_wrong_passwords_give_429():
    client, _ = client_with_hub(reply=lambda req: AppResponse(status=401, body={"code": "wrong_password"}))
    for _ in range(S.login_max_failures):
        assert client.post("/login", json={"password": "x"}).status_code == 401
    assert client.post("/login", json={"password": "x"}).status_code == 429


@pytest.mark.fr("FR-10")
def test_the_app_stop_goes_down_and_waits_for_stop_done():
    client, hub = client_with_hub()
    r = client.post("/stop")
    assert r.status_code == 200
    assert r.json() == {"ral": "stopped"}
    assert [m.type for m in hub.seen] == ["app.request", "stop"]


@pytest.mark.fr("FR-06")
def test_requests_go_to_the_enclave_and_the_answer_comes_back():
    enclave = FakeEnclave()
    gateway = HubGateway(enclave, live=None, app_timeout_s=1, stop_timeout_s=1)
    req = Envelope.model_validate(
        {
            "type": "decide",
            "id": "m-h-1",
            "run_id": "r-1",
            "sent_at": datetime.now(UTC),
            "payload": {
                "trigger": "time",
                "device": {"type": "light", "risk": "low"},
                "action": "off",
                "mapping": {"status": "Verified", "confidence": 0.9},
                "routine": None,
                "confidence": 0.9,
            },
        }
    )
    answer = asyncio.run(gateway.handle(req))
    assert answer.type == "decide.result" and answer.re == "m-h-1"
    assert enclave.asked == [req]


def test_an_enclave_that_fails_gives_no_answer():
    """The hub's time limit then decides on the safe side (wiki: Hub messages > Answers that do not come)."""
    app = create_app(S)  # the vsock link is not built yet
    req = Envelope.model_validate(
        {
            "type": "people",
            "id": "m-h-2",
            "run_id": "r-1",
            "sent_at": datetime.now(UTC),
            "payload": {"photo": sealed(), "work_area": [0, 0, 0.5, 1]},
        }
    )
    assert asyncio.run(app.state.hub.handle(req)) is None


def test_the_hub_needs_its_token(monkeypatch):
    monkeypatch.setenv("HUB_TOKEN", "t0k3n")
    client = TestClient(create_app(S))
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws/hub", headers={"Authorization": "Bearer nope"}) as ws,
    ):
        ws.receive_text()
    with client.websocket_connect("/ws/hub", headers={"Authorization": "Bearer t0k3n"}):
        assert client.get("/healthz").json()["hub"] is True


def test_detach_fails_the_waiting_calls():
    async def go():
        gateway = HubGateway(FakeEnclave(), live=None, app_timeout_s=5, stop_timeout_s=5)

        async def send(_msg):
            asyncio.get_running_loop().call_later(0.01, gateway.detach)

        gateway.attach(send)
        with pytest.raises(HubOffline):
            await gateway.stop()

    asyncio.run(go())
