"""A run from the hub's orchestrator to the enclave's handlers and back: the Reader seals each photo, the
enclave opens it (a fake KMS) and answers from ai_engine's FakeEngine. Only the WebSocket, the server and
the enclave link are left out; they relay envelopes unopened (tests/server, test_enclave_link.py)."""

import asyncio

import numpy as np
import pytest
from ai_engine import CheckResult, PersonBox
from ai_engine.testing import FakeEngine
from sqlalchemy import select

from backend.common.settings import HubSettings, RiskSettings, load
from backend.enclave.handlers import Handlers
from backend.enclave.unseal import opener
from backend.hub.answers import LinkAnswers
from backend.hub.camera import Reader
from backend.hub.db.models import Device
from backend.hub.link import envelope
from tests.contract.test_sealing import FakeKms
from tests.hub.test_scenarios import Hub

S = load(HubSettings, "hub.yaml")
ENGINE_SETTINGS = {"paths": {"fingerprint_weights": None}, "refind": {"max_shift": 0.1}, "people": {"conf": 0.25}}
LIGHT = "Living room ceiling light"  # no wireless channel: the press is the way


class Camera:
    def isOpened(self):  # noqa: N802
        return True

    def read(self):
        return True, np.full((480, 640, 3), 128, np.uint8)

    def release(self):
        pass


def wire(h: Hub, engine: FakeEngine):
    kms = FakeKms()
    handlers = Handlers(engine, ENGINE_SETTINGS, load(RiskSettings, "risk.yaml"), opener(kms))
    asked = []

    async def ask(type_, payload, timeout_s):
        asked.append(type_)
        return handlers.answer(envelope(type_, payload))

    reader = Reader(0, S.photo_max_side, S.photo_jpeg_quality, 0, keys=lambda: kms, open_capture=lambda i: Camera())
    h.orch.answers = LinkAnswers(ask, reader.photo, h.db, S)
    h.orch.speed = 1000
    return asked


async def turn_off_the_light(h: Hub):
    with h.db() as s:
        dev = s.scalar(select(Device).where(Device.name == LIGHT)).device_id
    task = asyncio.create_task(h.orch.handle_situation("test", "sensor", "A test.", [(dev, "off")], paused=False))
    while not task.done():  # the policy may ask first: the resident says yes
        for situation_id in list(h.orch.waiting):
            h.orch.answer(situation_id, True)
        await asyncio.sleep(0.01)
    return await task


def presses(h):
    return [c for c in h.ral.calls if c[0] == "press"]


@pytest.mark.fr("FR-08")
@pytest.mark.fr("FR-10")
def test_a_clear_gate_and_a_yes_check_press_once_and_succeed():
    async def go():
        h = Hub()
        engine = FakeEngine()
        engine.script("find_people", [])
        engine.script(
            "check_effect",
            CheckResult(
                main="yes", second="yes", answer="yes", disagree=False, measures={}, confidence=0.9, reason="darker"
            ),
        )
        asked = wire(h, engine)
        await turn_off_the_light(h)
        run = h.runs()[-1]
        assert asked == ["decide", "people", "check"]
        assert len(presses(h)) == 1
        assert run.result == "success"
        before, after = engine.calls[-1][1][:2]
        assert len(before) == len(after) == 1  # the gate's photo, then one after the press

    asyncio.run(go())


@pytest.mark.fr("FR-10")
def test_a_person_in_the_work_area_cancels_the_press():
    async def go():
        h = Hub()
        engine = FakeEngine()
        engine.script("find_people", [PersonBox(kind="person", box=(0.2, 0.1, 0.4, 0.9), confidence=0.8, reason="x")])
        asked = wire(h, engine)
        await turn_off_the_light(h)
        run = h.runs()[-1]
        assert asked == ["decide", "people"]
        assert presses(h) == []
        assert (run.result, run.entry["reason"]) == ("cancelled", "person in the work area")

    asyncio.run(go())


@pytest.mark.fr("FR-10")
def test_an_engine_error_on_the_gate_means_no_press():
    """FakeEngine raises when find_people is not scripted: the enclave answers nothing (C-04)."""

    async def go():
        h = Hub()
        wire(h, FakeEngine())
        await turn_off_the_light(h)
        run = h.runs()[-1]
        assert presses(h) == []
        assert (run.result, run.entry["reason"]) == ("cancelled", "no answer from the safety gate")

    asyncio.run(go())
