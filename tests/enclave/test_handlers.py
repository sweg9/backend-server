"""The enclave handlers with ai_engine's FakeEngine: requests in the shapes of the hub messages, answers
from the scripted engine (wiki: Hub messages, ai-engine interface)."""

import logging
from datetime import UTC, datetime

import numpy as np
import pytest
from ai_engine import (
    ChangeResult,
    ClearResult,
    ControlBox,
    DeviceBox,
    EngineError,
    Gang,
    LinkGuess,
    PersonBox,
    PressPlan,
    RefindResult,
    StateResult,
)
from ai_engine import CheckResult as EngineCheck
from ai_engine.testing import FakeEngine

from backend.common.messages import Envelope
from backend.common.settings import RiskSettings, load
from backend.enclave.handlers import Handlers

RISK = load(RiskSettings, "risk.yaml")
ENGINE_SETTINGS = {"paths": {"fingerprint_weights": None}, "refind": {"max_shift": 0.1}, "people": {"conf": 0.25}}
FP = [0.0] * 383 + [1.0]


def sealed(pid):
    return {"photo_id": pid, "key": "AQID", "nonce": "BAUG", "data": "BwgJ", "w": 80, "h": 60}


class Photos:
    """Stands in for unseal.py: every photo opens to a gray frame; records what was opened."""

    def __init__(self):
        self.opened = []

    def __call__(self, s):
        self.opened.append(s.photo_id)
        return np.full((60, 80, 3), 128, dtype=np.uint8)


def handlers(fake, settings=ENGINE_SETTINGS):
    return Handlers(fake, settings, RISK, Photos())


def env(type_, payload, run_id=None):
    return Envelope(type=type_, id="m_req", run_id=run_id, sent_at=datetime.now(UTC), payload=payload)


def control(kind="switch", box=(0.40, 0.30, 0.45, 0.40), conf=0.9):
    return ControlBox(kind=kind, box=box, confidence=conf, reason=f"{kind} found with score {conf:.2f}")


def guess(count, index, cls="ceiling_light", p=0.7):
    return LinkGuess(gang=Gang(count=count, index=index), device_class=cls, p=p, confidence=p, reason="x")


NO_GUESS = LinkGuess(gang=None, device_class=None, p=0.0, confidence=0.0, reason="no link guess for a toggle")
CLEAR = ClearResult(clear=True, sharpness=200.0, confidence=0.9, reason="clear")
OFF = StateResult(state="off", confidence=0.93, reason="looks off (0.93)")


def person(kind="person", box=(0.1, 0.1, 0.3, 0.9), conf=0.8):
    return PersonBox(kind=kind, box=box, confidence=conf, reason="x")


# ---------- recognize (FR-01, FR-02)


def scripted_scan(fake, controls, guesses, people=()):
    fake.script("find_people", list(people))
    fake.script("detect_controls", controls)
    fake.script("link_guess", guesses)
    for _ in controls:
        fake.script("is_clear", CLEAR)
        fake.script("read_state", OFF)


@pytest.mark.fr("FR-01")
def test_recognize_answers_every_control():
    fake = FakeEngine()
    toggle = control("toggle", (0.7, 0.3, 0.75, 0.4))
    scripted_scan(fake, [control(), toggle], [guess(2, 0), NO_GUESS])
    a = handlers(fake).answer(
        env("recognize", {"purpose": "scan", "spot_id": "s1", "room": "living_room", "photo": sealed("ph1")})
    )
    assert a.type == "recognize.result" and a.re == "m_req"
    body = a.body()
    assert body.person is False
    first, second = body.controls
    assert (first.kind, first.state, first.clear, first.score) == ("switch", "off", True, 0.9)
    assert (first.gang.count, first.gang.index) == (2, 1)  # 1-based on the wire
    assert first.link_guess.device_class == "ceiling_light"
    assert second.gang is None and second.link_guess is None
    assert first.control_id is None


@pytest.mark.fr("FR-01")
def test_recognize_reports_a_person():
    """C-05: the hub saves nothing from a photo with a person in it."""
    fake = FakeEngine()
    scripted_scan(fake, [], [], [person()])
    a = handlers(fake).answer(
        env("recognize", {"purpose": "scan", "spot_id": "s1", "room": "bedroom", "photo": sealed("ph1")})
    )
    assert a.body().person is True


@pytest.mark.fr("FR-02")
def test_watch_matches_known_controls():
    fake = FakeEngine()
    near = control(box=(0.41, 0.31, 0.46, 0.41))
    far = control(box=(0.80, 0.30, 0.85, 0.40))
    scripted_scan(fake, [near, far], [guess(1, 0), guess(1, 0)])
    known = [
        {"control_id": "ctl_A", "kind": "switch", "box": [0.40, 0.30, 0.45, 0.40]},
        {"control_id": "ctl_B", "kind": "toggle", "box": [0.80, 0.30, 0.85, 0.40]},
    ]
    a = handlers(fake).answer(
        env(
            "recognize",
            {"purpose": "watch", "spot_id": "s1", "room": "living_room", "photo": sealed("ph1"), "known": known},
        )
    )
    assert [c.control_id for c in a.body().controls] == ["ctl_A", None]  # ctl_B is a toggle


def test_recognize_with_a_failed_model_gets_no_answer(caplog):
    fake = FakeEngine()
    fake.script("find_people", EngineError("people detector failed"))
    with caplog.at_level(logging.WARNING, logger="backend"):
        a = handlers(fake).answer(
            env("recognize", {"purpose": "scan", "spot_id": "s1", "room": "living_room", "photo": sealed("ph1")})
        )
    assert a is None
    assert "EngineError" in caplog.text and "ph1" not in caplog.text  # L-07: no payload in the log


# ---------- check (FR-02, FR-08)


def change(clear, direction, score=0.5):
    return ChangeResult(
        change_score=score,
        clear=clear,
        direction=direction,
        confidence=0.8,
        reason=f"{score:.0%} of the region got {direction}",
    )


MAP = {
    "purpose": "map",
    "before": [sealed("b1")],
    "after": [sealed("a1")],
    "device_box": None,
    "device_type": None,
    "action": None,
}


@pytest.mark.fr("FR-02")
def test_map_names_the_device_that_changed_most():
    fake = FakeEngine()
    lamp = DeviceBox(type="light", box=(0.2, 0.1, 0.4, 0.3), confidence=0.9, reason="light found")
    fan = DeviceBox(type="fan", box=(0.6, 0.1, 0.8, 0.3), confidence=0.9, reason="fan found")
    fake.script("measure_change", change(True, "brighter"), change(True, "brighter", 0.9), change(False, "none", 0.0))
    fake.script("detect_devices", [lamp, fan])
    r = handlers(fake).answer(env("check", MAP)).body()
    assert (r.change, r.device_type, r.answer) == ("off_to_on", "light", None)
    assert r.device_box == (0.2, 0.1, 0.4, 0.3)


@pytest.mark.fr("FR-02")
def test_map_without_a_device_cannot_verify():
    """C-08: a clear change but no device found leaves the device empty."""
    fake = FakeEngine()
    fake.script("measure_change", change(True, "darker"))
    fake.script("detect_devices", [])
    r = handlers(fake).answer(env("check", MAP)).body()
    assert (r.change, r.device_type, r.device_box) == ("on_to_off", None, None)


@pytest.mark.fr("FR-02")
def test_map_without_a_clear_change():
    fake = FakeEngine()
    fake.script("measure_change", change(False, "none", 0.1))
    r = handlers(fake).answer(env("check", MAP)).body()
    assert (r.change, r.device_type) == ("none", None)
    assert not [c for c, _ in fake.calls if c == "detect_devices"]


def verify(action="on"):
    return {
        "purpose": "verify",
        "before": [sealed("b1")],
        "after": [sealed("a1"), sealed("a2")],
        "device_box": [0.2, 0.1, 0.4, 0.3],
        "device_type": "light",
        "action": action,
    }


def effect(answer):
    return EngineCheck(
        main=answer,
        second=None,
        answer=answer,
        disagree=False,
        measures={},
        confidence=0.8,
        reason="lamp area 40% brighter",
    )


@pytest.mark.fr("FR-08")
@pytest.mark.parametrize(
    ("action", "answer", "expected"),
    [("on", "yes", "off_to_on"), ("off", "yes", "on_to_off"), ("on", "no", "none"), ("on", "unsure", "none")],
)
def test_verify_passes_the_effect_check(action, answer, expected):
    fake = FakeEngine()
    fake.script("check_effect", effect(answer))
    h = handlers(fake)
    r = h.answer(env("check", verify(action))).body()
    assert (r.answer, r.change, r.device_type) == (answer, expected, "light")
    _, (before, after, box, device_type, act) = fake.calls[-1]
    assert (len(before), len(after), device_type, act) == (1, 2, "light", action)
    assert h.open.opened == ["b1", "a1", "a2"]


# ---------- refind (FR-09)


def refind(fingerprint=None):
    return {
        "photo": sealed("p1"),
        "control": {
            "control_id": "ctl_A",
            "kind": "switch",
            "box": [0.40, 0.30, 0.45, 0.40],
            "fingerprint": fingerprint,
        },
        "actuators": [{"id": "A1", "faces": [0.0, 0.0, 0.5, 1.0]}],
    }


FOUND = RefindResult(found=True, box=(0.41, 0.31, 0.46, 0.41), similarity=0.9, confidence=0.9, reason="found again")
LOST = RefindResult(found=False, box=None, similarity=None, confidence=0.9, reason="no switch near the saved place")


@pytest.mark.fr("FR-09")
def test_refind_plans_the_press():
    fake = FakeEngine()
    fake.script("refind_control", FOUND)
    fake.script("plan_press", PressPlan(actuator="A1", push_ms=400, confidence=0.9, reason="actuator A1 faces it"))
    r = handlers(fake).answer(env("refind", refind(), run_id="r_1")).body()
    assert r.found and r.plan.actuator == "A1" and r.plan.push_ms == 400


@pytest.mark.fr("FR-09")
def test_refind_not_found_has_no_plan():
    fake = FakeEngine()
    fake.script("refind_control", LOST)
    a = handlers(fake).answer(env("refind", refind(), run_id="r_1"))
    assert a.run_id == "r_1"
    r = a.body()
    assert not r.found and r.box is None and r.plan is None
    assert "plan_press" not in [c for c, _ in fake.calls]


@pytest.mark.fr("FR-09")
def test_refind_without_a_facing_actuator_has_no_plan():
    fake = FakeEngine()
    fake.script("refind_control", FOUND)
    fake.script("plan_press", PressPlan(actuator=None, push_ms=400, confidence=1.0, reason="no actuator faces it"))
    r = handlers(fake).answer(env("refind", refind(), run_id="r_1")).body()
    assert r.found and r.plan is None and r.reason == "no actuator faces it"


@pytest.mark.fr("FR-09")
def test_refind_passes_the_fingerprint_only_with_a_fingerprint_model():
    fake = FakeEngine()
    fake.script("refind_control", LOST, LOST)
    handlers(fake).answer(env("refind", refind(FP), run_id="r_1"))
    assert fake.calls[-1][1][1].fingerprint is None
    with_model = {**ENGINE_SETTINGS, "paths": {"fingerprint_weights": "models/dinov2-small.pt"}}
    handlers(fake, with_model).answer(env("refind", refind(FP), run_id="r_1"))
    fp = fake.calls[-1][1][1].fingerprint
    assert fp.model == "models/dinov2-small.pt" and len(fp.vector) == 384


# ---------- people (FR-10, C-04)


def people_req():
    return {"photo": sealed("p1"), "work_area": [0.4, 0.2, 0.6, 0.6]}


@pytest.mark.fr("FR-10")
def test_a_person_in_the_work_area_is_not_clear():
    fake = FakeEngine()
    fake.script("find_people", [person("cat", (0.5, 0.5, 0.7, 0.9), 0.6)])
    r = handlers(fake).answer(env("people", people_req(), run_id="r_1")).body()
    assert not r.clear and r.reason == "cat in the work area"


@pytest.mark.fr("FR-10")
def test_people_outside_the_work_area_are_clear():
    fake = FakeEngine()
    fake.script("find_people", [person(box=(0.0, 0.0, 0.3, 0.9))])
    r = handlers(fake).answer(env("people", people_req(), run_id="r_1")).body()
    assert r.clear and r.confidence == pytest.approx(0.75)


@pytest.mark.fr("FR-10")
def test_people_without_an_answer_from_the_model_gets_no_answer():
    """No answer means no press on the hub (wiki: Answers that do not come)."""
    fake = FakeEngine()  # find_people raises when nothing is scripted
    assert handlers(fake).answer(env("people", people_req(), run_id="r_1")) is None


# ---------- decide (FR-06) and the envelope


@pytest.mark.fr("FR-06")
def test_decide_uses_the_policy():
    payload = {
        "trigger": "time",
        "device": {"type": "heater", "risk": "high"},
        "action": "on",
        "mapping": {"status": "Verified", "confidence": 0.9},
        "routine": None,
        "confidence": 0.99,
    }
    a = handlers(FakeEngine()).answer(env("decide", payload, run_id="r_1"))
    assert a.type == "decide.result" and a.body().decision == "ask"


def test_not_an_enclave_request_gets_no_answer():
    stop = Envelope(type="stop", id="m_s", run_id=None, sent_at=datetime.now(UTC), payload={"source": "app"})
    assert handlers(FakeEngine()).answer(stop) is None
