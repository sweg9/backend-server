"""app.py answers every route of openapi/backend-server.yaml from the hub's database (wiki: Hub messages)."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from argon2 import PasswordHasher
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from backend.common.messages import AppRequest
from backend.hub.app import AppHandlers
from backend.hub.db.models import Base
from backend.hub.db.session import make_engine, make_sessionmaker
from backend.hub.seed import NotEmpty, seed_demo

SPEC = yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi" / "backend-server.yaml").read_text())
REGISTRY = Registry().with_resource(
    "spec", Resource.from_contents({**SPEC, "$schema": "https://json-schema.org/draft/2020-12/schema"})
)
HASH = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("pw")
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _refs(schema):
    if isinstance(schema, dict):
        return {k: ("spec" + v if k == "$ref" and v.startswith("#") else _refs(v)) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_refs(x) for x in schema]
    return schema


def check_schema(method: str, path: str, status: int, body) -> None:
    content = SPEC["paths"][path][method.lower()]["responses"][str(status)].get("content")
    if content:
        schema = _refs(content["application/json"]["schema"])
        Draft202012Validator(schema, registry=REGISTRY).validate(body)


class Hub:
    def __init__(self):
        self.engine = make_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = make_sessionmaker(self.engine)
        with self.db() as s:
            seed_demo(s, "Asia/Seoul", now=NOW)
        self.events: list[tuple[str, dict]] = []

        async def emit(kind, data):
            self.events.append((kind, data))

        self.app = AppHandlers(self.db, 30, HASH, emit)
        self.session: str | None = None

    def call(self, method, path, body=None, query=None, session="mine"):
        req = AppRequest(
            method=method,
            path=path,
            body=body,
            query=query or {},
            session=self.session if session == "mine" else session,
        )
        return asyncio.run(self.app.handle(req))

    def sign_in(self):
        self.session = self.call("POST", "/login", {"password": "pw"}, session=None).set_session
        return self


@pytest.fixture
def hub():
    return Hub().sign_in()


def test_login_checks_the_password_and_makes_a_session():
    h = Hub()
    assert h.call("POST", "/login", {"password": "nope"}, session=None).status == 401
    r = h.call("POST", "/login", {"password": "pw"}, session=None)
    assert r.status == 204 and r.set_session
    assert h.call("GET", "/me", session=r.set_session).status == 200


def test_every_route_but_login_needs_a_session(hub):
    for m, p in [("GET", "/me"), ("GET", "/controls"), ("POST", "/stop"), ("PUT", "/proactive")]:
        r = hub.call(m, p, session="made-up")
        assert r.status == 401 and r.body["code"] == "signed_out"


def test_logout_ends_the_session_for_good(hub):
    r = hub.call("POST", "/logout")
    assert r.status == 204 and r.set_session == ""
    assert hub.call("GET", "/me").status == 401


@pytest.mark.parametrize("path", ["/me", "/controls", "/devices", "/mappings", "/routines", "/runs"])
def test_lists_match_the_spec(hub, path):
    r = hub.call("GET", path)
    assert r.status == 200
    check_schema("GET", path, 200, r.body)


@pytest.mark.fr("FR-12")
def test_runs_are_newest_first_with_explanations(hub):
    runs = hub.call("GET", "/runs").body
    assert len(runs) == 10
    assert [r["started_at"] for r in runs] == sorted((r["started_at"] for r in runs), reverse=True)
    assert all(r["explanation"].startswith("I ") for r in runs)
    assert len(hub.call("GET", "/runs", query={"limit": "3"}).body) == 3
    assert hub.call("GET", "/runs", query={"limit": "0"}).status == 422


@pytest.mark.fr("FR-02")
def test_mappings_filter_confirm_and_reject(hub):
    pending = hub.call("GET", "/mappings", query={"status": "Pending"}).body
    assert len(pending) == 1 and pending[0]["device_id"] is None
    assert hub.call("POST", f"/mappings/{pending[0]['mapping_id']}/confirm").status == 409  # no device yet
    stale = hub.call("GET", "/mappings", query={"status": "Stale"}).body[0]
    r = hub.call("POST", f"/mappings/{stale['mapping_id']}/confirm")
    assert r.status == 200 and r.body["status"] == "Verified"
    assert hub.events == [("mapping", r.body)]
    assert hub.call("POST", f"/mappings/{stale['mapping_id']}/reject").body["status"] == "Rejected"
    assert hub.call("POST", "/mappings/mp_nope/confirm").status == 404


@pytest.mark.fr("FR-04")
def test_routines_by_hand_need_a_verified_mapping(hub):
    stale = hub.call("GET", "/mappings", query={"status": "Stale"}).body[0]["mapping_id"]
    assert hub.call("POST", "/routines", {"mapping_id": stale, "action": "on"}).status == 409  # C-08
    verified = hub.call("GET", "/mappings", query={"status": "Verified"}).body[0]["mapping_id"]
    r = hub.call("POST", "/routines", {"mapping_id": verified, "action": "on", "time": "21:00"})
    assert r.status == 201
    check_schema("POST", "/routines", 201, r.body)
    assert r.body["source"] == "manual" and r.body["independence"] == "ask_first"
    assert r.body in hub.call("GET", "/routines").body
    assert hub.call("POST", "/routines", {"mapping_id": verified, "action": "on", "time": "25:00"}).status == 422


@pytest.mark.fr("FR-04")
def test_feedback_changes_or_removes_a_routine(hub):
    rt = hub.call("GET", "/routines").body[0]["routine_id"]
    r = hub.call("POST", f"/routines/{rt}/feedback", {"decision": "change", "time": "22:15"})
    assert r.body["time"] == "22:15"
    hub.call("POST", f"/routines/{rt}/feedback", {"decision": "remove"})
    assert rt not in [x["routine_id"] for x in hub.call("GET", "/routines").body]
    assert hub.events[-1][1].get("removed") is True
    assert hub.call("POST", f"/routines/{rt}/feedback", {"decision": "keep"}).status == 404


@pytest.mark.fr("FR-06")
def test_device_correction_and_proactive_pause(hub):
    dev = hub.call("GET", "/devices").body[0]
    r = hub.call("PATCH", f"/devices/{dev['device_id']}", {"name": "Big light", "risk": "high"})
    assert (r.body["name"], r.body["risk"]) == ("Big light", "high")
    assert hub.call("PATCH", f"/devices/{dev['device_id']}", {"colour": "red"}).status == 422
    assert hub.call("PUT", "/proactive", {"paused": True}).body == {"paused": True}


def test_unknown_routes_and_methods(hub):
    assert hub.call("GET", "/nothing").status == 404
    assert hub.call("DELETE", "/routines").status == 405


def test_the_demo_seed_refuses_a_database_with_records():
    h = Hub()
    with h.db() as s, pytest.raises(NotEmpty):
        seed_demo(s, "Asia/Seoul")


def test_seeded_intents_read_well(hub):
    intents = {r["intent"] for r in hub.call("GET", "/runs").body}
    assert "turn off the TV" in intents and "turn off the living room ceiling light" in intents


@pytest.mark.fr("FR-09")
def test_map_draws_every_control_and_device_of_the_demo_home(hub):
    r = hub.call("GET", "/map")
    assert r.status == 200
    check_schema("GET", "/map", 200, r.body)
    controls = {c["control_id"] for c in hub.call("GET", "/controls").body}
    devices = {d["device_id"] for d in hub.call("GET", "/devices").body}
    assert {c["control_id"] for c in r.body["controls"]} == controls
    assert {d["device_id"] for d in r.body["devices"]} == devices
    assert r.body["rooms"] and r.body["walls"]


@pytest.mark.fr("FR-09")
def test_a_control_found_later_stands_beside_the_others_at_its_spot(hub):
    from sqlalchemy import select

    from backend.hub.db.models import Control
    from backend.hub.registry import add_control

    with hub.db() as s:
        known = s.scalar(select(Control).where(Control.press_spot_id == "living_wall_1"))
        new = add_control(s, "switch", "off", 0.9, ["press"], [0.5, 0.4], "living_wall_1")
        s.commit()
        known_id, new_id = known.control_id, new.control_id
    at = {c["control_id"]: c for c in hub.call("GET", "/map").body["controls"]}
    assert at[new_id]["spot_id"] == at[known_id]["spot_id"] == "living_wall_1"
    assert at[new_id]["press_spot"] == at[known_id]["press_spot"]
    assert at[new_id]["at"]["x"] > at[known_id]["at"]["x"]


@pytest.mark.fr("FR-02")
def test_changing_the_device_of_a_mapping_sends_it_back_to_pending(hub):
    verified = next(m for m in hub.call("GET", "/mappings").body if m["status"] == "Verified")
    other = next(d for d in hub.call("GET", "/devices").body if d["device_id"] != verified["device_id"])
    path = f"/mappings/{verified['mapping_id']}"

    r = hub.call("PATCH", path, {"device_id": other["device_id"]})
    assert r.status == 200
    check_schema("PATCH", "/mappings/{mapping_id}", 200, r.body)
    assert r.body["device_id"] == other["device_id"] and r.body["status"] == "Pending"  # C-08
    event = [d for k, d in hub.events if k == "mapping"][-1]
    assert event["previous_device_id"] == verified["device_id"]

    assert hub.call("POST", f"{path}/confirm").body["status"] == "Verified"
    assert hub.call("PATCH", path, {"device_id": other["device_id"]}).body["status"] == "Verified"  # no change


def test_changing_a_mapping_checks_its_input(hub):
    m = hub.call("GET", "/mappings").body[0]
    path = f"/mappings/{m['mapping_id']}"
    assert hub.call("PATCH", path, {"device_id": "dev_nope"}).body["code"] == "unknown_device"
    assert hub.call("PATCH", "/mappings/mp_nope", {"device_id": m["device_id"] or "x"}).status == 404
    assert hub.call("PATCH", path, {}).status == 422
    assert hub.call("PATCH", path, {"device_id": "x", "status": "Verified"}).status == 422
