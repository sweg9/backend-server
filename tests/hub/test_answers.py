"""The real answers over the link (answers.py) and the Reader (camera.py), with a fake link and camera:
what the hub asks the enclave, and what it does when no answer or no photo comes (wiki: Hub messages >
Answers that do not come)."""

import asyncio
import tempfile
from datetime import UTC, datetime

import numpy as np
import pytest
from sqlalchemy import select

from backend.common.messages import Envelope, Sealed
from backend.common.sealing import SealError
from backend.common.settings import HubSettings, load
from backend.common.vocab import Channel, CheckAnswer, Decision, DeviceAction, Trigger
from backend.hub.answers import WHOLE_VIEW, LinkAnswers, reach
from backend.hub.camera import Reader
from backend.hub.channels.base import SendResult
from backend.hub.db.models import Base, Device, Feedback, Mapping, Routine
from backend.hub.db.session import make_engine, make_sessionmaker
from backend.hub.seed import seed_demo
from tests.conftest import sealed

S = load(HubSettings, "hub.yaml")
LIGHT = "Living room ceiling light"


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        engine = make_engine(f"sqlite:///{d}/hub.db")
        Base.metadata.create_all(engine)
        maker = make_sessionmaker(engine)
        with maker() as s:
            seed_demo(s, "Asia/Seoul")
        yield maker
        engine.dispose()


def device_id(db, name=LIGHT):
    with db() as s:
        return s.scalar(select(Device.device_id).where(Device.name == name))


class Link:
    """Answers each request type with the payload given, or not at all (None)."""

    def __init__(self, **answers):
        self.answers = answers
        self.asked: list[tuple[str, dict, float]] = []

    async def __call__(self, type_, payload, timeout_s):
        self.asked.append((type_, payload.model_dump(mode="json"), timeout_s))
        body = self.answers.get(type_)
        if body is None:
            return None
        return Envelope(
            type=f"{type_}.result", id="m-e", re="m-h", run_id=None, sent_at=datetime.now(UTC), payload=body
        )


class Photos:
    def __init__(self, give=True):
        self.give = give
        self.taken = 0

    async def __call__(self):
        if not self.give:
            return None
        self.taken += 1
        return Sealed.model_validate(sealed(f"ph_{self.taken}"))


def item(db, name=LIGHT, confidence=0.9, action=DeviceAction.OFF):
    d = device_id(db, name)
    with db() as s:
        m = s.scalar(select(Mapping).where(Mapping.device_id == d))
        r = s.scalar(select(Routine).where(Routine.mapping_id == m.mapping_id))
    return {
        "device_id": d,
        "action": action,
        "confidence": confidence,
        "routine_id": r.routine_id if r else None,
        "independence": r.independence if r else None,
    }


def run(coro):
    return asyncio.run(coro)


# --- decide (FR-05, FR-06) -------------------------------------------------------------------------


@pytest.mark.fr("FR-06")
def test_decide_asks_the_enclave_with_the_device_and_the_trigger(db):
    link = Link(decide={"decision": "act", "confidence": 0.9, "reason": "low risk, confidence 0.90"})
    a = LinkAnswers(link, Photos(), db, S)
    assert run(a.decide(item(db), False, Trigger.SENSOR)) == Decision.ACT
    type_, payload, timeout = link.asked[0]
    assert type_ == "decide" and timeout == S.decide_timeout_s
    assert payload["trigger"] == "sensor" and payload["device"]["type"] == "light"
    assert payload["mapping"] == {"status": "Verified", "confidence": 0.9}


@pytest.mark.fr("FR-05")
def test_no_decide_answer_only_suggests(db):
    assert run(LinkAnswers(Link(), Photos(), db, S).decide(item(db), False, Trigger.TIME)) == Decision.SUGGEST


@pytest.mark.fr("FR-06")
def test_a_paused_home_only_suggests_without_asking(db):
    link = Link(decide={"decision": "act", "confidence": 0.9, "reason": "x"})
    assert run(LinkAnswers(link, Photos(), db, S).decide(item(db), True, Trigger.TIME)) == Decision.SUGGEST
    assert link.asked == []


def test_the_accept_share_counts_accept_and_reject_answers(db):
    it = item(db)
    assert it["routine_id"] is not None, "the demo home has a routine for this light"
    with db() as s:
        for i, kind in enumerate(["accept", "accept", "reject", "keep"]):
            s.add(Feedback(feedback_id=f"fb_{i}", routine_id=it["routine_id"], kind=kind, at=datetime.now(UTC)))
        s.commit()
    link = Link(decide={"decision": "ask", "confidence": 0.9, "reason": "x"})
    run(LinkAnswers(link, Photos(), db, S).decide(it, False, Trigger.ROUTINE))
    assert link.asked[0][1]["routine"]["accept_share"] == pytest.approx(2 / 3)


# --- the safety gate (FR-10, C-04) ----------------------------------------------------------------


@pytest.mark.fr("FR-10")
def test_the_gate_asks_people_over_the_hands_reach():
    link = Link(people={"clear": True, "confidence": 0.75, "reason": "no person or pet in the work area"})
    a = LinkAnswers(link, Photos(), None, S)
    assert run(a.gate("dev_1")) == (True, None)
    type_, payload, timeout = link.asked[0]
    assert type_ == "people" and timeout == S.people_timeout_s
    assert tuple(payload["work_area"]) == reach(S) == (0.0, 0.0, 1.0, 1.0)


@pytest.mark.fr("FR-10")
@pytest.mark.parametrize(
    ("link", "photos", "why"),
    [
        (Link(people={"clear": False, "confidence": 0.8, "reason": "person in the work area"}), Photos(), "person"),
        (Link(), Photos(), "no answer from the safety gate"),
        (Link(people={"clear": True, "confidence": 0.8, "reason": "x"}), Photos(give=False), "no photo"),
    ],
)
def test_the_gate_fails_on_a_person_no_answer_or_no_photo(link, photos, why):
    ok, reason = run(LinkAnswers(link, photos, None, S).gate("dev_1"))
    assert not ok and why in reason


@pytest.mark.fr("FR-10")
def test_the_route_gate_looks_at_the_whole_view():
    link = Link(people={"clear": True, "confidence": 0.75, "reason": "x"})
    assert run(LinkAnswers(link, Photos(), None, S).route_gate("hall_wall_1")) == (True, None)
    assert tuple(link.asked[0][1]["work_area"]) == WHOLE_VIEW


# --- the effect check (FR-08) ---------------------------------------------------------------------

YES = {
    "change": "on_to_off",
    "answer": "yes",
    "device_box": [0.3, 0.2, 0.6, 0.7],
    "device_type": "light",
    "confidence": 0.9,
    "reason": "lamp area 40% darker",
}


@pytest.mark.fr("FR-08")
def test_after_a_press_the_gate_photo_is_the_before_photo(db):
    d = device_id(db)
    link = Link(people={"clear": True, "confidence": 0.75, "reason": "x"}, check=YES)
    a = LinkAnswers(link, Photos(), db, S)
    run(a.gate(d))
    assert run(a.verify(d, Channel.ARM, DeviceAction.OFF, None)) == (CheckAnswer.YES, None)
    type_, payload, timeout = link.asked[1]
    assert type_ == "check" and timeout == S.check_timeout_s
    assert payload["purpose"] == "verify" and payload["device_type"] == "light" and payload["action"] == "off"
    assert [p["photo_id"] for p in payload["before"]] == ["ph_1"]
    assert [p["photo_id"] for p in payload["after"]] == ["ph_2"]


@pytest.mark.fr("FR-08")
def test_the_second_look_of_an_unsure_check_keeps_the_before_photo(db):
    d = device_id(db)
    unsure = {**YES, "change": "none", "answer": "unsure", "reason": "lamp area 3% darker"}
    link = Link(people={"clear": True, "confidence": 0.75, "reason": "x"}, check=unsure)
    a = LinkAnswers(link, Photos(), db, S)
    run(a.gate(d))
    assert run(a.verify(d, Channel.ARM, DeviceAction.OFF, None)) == (CheckAnswer.UNSURE, "lamp area 3% darker")
    run(a.verify(d, Channel.ARM, DeviceAction.OFF, None))
    assert [p["before"][0]["photo_id"] for t, p, _ in link.asked if t == "check"] == ["ph_1", "ph_1"]


@pytest.mark.fr("FR-08")
def test_a_platform_report_answers_without_the_camera(db):
    photos = Photos()
    a = LinkAnswers(Link(), photos, db, S)
    run(a.look_before("dev_1", Channel.WIFI))
    sent = SendResult(sent=True, reported_state="on", latency_ms=80)
    assert run(a.verify("dev_1", Channel.WIFI, DeviceAction.ON, sent)) == (CheckAnswer.YES, None)
    no = run(a.verify("dev_1", Channel.WIFI, DeviceAction.OFF, sent))
    assert no == (CheckAnswer.NO, "the platform says the device is on")
    assert photos.taken == 0


@pytest.mark.fr("FR-08")
def test_an_ir_command_is_checked_by_the_camera(db):
    d = device_id(db)
    link = Link(check=YES)
    a = LinkAnswers(link, Photos(), db, S)
    run(a.look_before(d, Channel.IR))
    sent = SendResult(sent=True, reported_state=None, latency_ms=40)
    assert run(a.verify(d, Channel.IR, DeviceAction.OFF, sent)) == (CheckAnswer.YES, None)


@pytest.mark.fr("FR-08")
@pytest.mark.parametrize(
    ("setup", "why"),
    [
        ("no_before", "no photo from before the command"),
        ("no_answer", "no check answer"),
        ("failed_gate", "no photo from before the command"),
    ],
)
def test_the_check_is_unsure_without_photos_or_an_answer(db, setup, why):
    d = device_id(db)
    people = "clear" if setup != "failed_gate" else "blocked"
    link = Link(
        people={"clear": people == "clear", "confidence": 0.8, "reason": "person in the work area"},
        check=None if setup == "no_answer" else YES,
    )
    a = LinkAnswers(link, Photos(), db, S)
    if setup != "no_before":
        run(a.gate(d))
    assert run(a.verify(d, Channel.ARM, DeviceAction.OFF, None)) == (CheckAnswer.UNSURE, why)


# --- the Reader (C-05) ----------------------------------------------------------------------------


class Camera:
    def __init__(self, frames=None, opened=True):
        self.frames = frames if frames is not None else []
        self.opened = opened
        self.reads = 0
        self.released = False

    def isOpened(self):  # noqa: N802
        return self.opened and not self.released

    def read(self):
        self.reads += 1
        return (True, self.frames.pop(0)) if self.frames else (False, None)

    def release(self):
        self.released = True


class Keys:
    def new_key(self):
        return b"k" * 32, b"wrapped"


def reader(camera, keys=Keys):
    return Reader(0, 1280, 85, 2, keys=keys, open_capture=lambda i: camera)


def test_the_reader_seals_one_fresh_photo_at_most_photo_max_side():
    frames = [np.zeros((1080, 1920, 3), np.uint8) for _ in range(3)]
    cam = Camera(frames)
    r = reader(cam)
    p = run(r.photo())
    assert (p.w, p.h) == (1280, 720) and p.photo_id.startswith("ph_")
    assert cam.reads == 3  # two flushed, one kept
    assert r.up


def test_no_frame_means_no_photo_and_the_camera_is_down():
    r = reader(Camera([]))
    assert run(r.photo()) is None and not r.up


def test_without_a_kms_key_no_photo_leaves_the_home():
    def no_keys():
        raise SealError("kms_key_id is not set in settings/hub.yaml, so photos stay at home")

    r = reader(Camera([np.zeros((60, 80, 3), np.uint8)] * 3), keys=no_keys)
    assert run(r.photo()) is None
