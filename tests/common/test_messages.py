"""The hub messages of the wiki parse, and the rules in their tables hold."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.common.messages import Envelope, RunResultMsg, RunStart
from tests.conftest import FP, crop

EXAMPLES = {
    "scan.start": {"scan_id": "sc-0003", "spot_id": "living_wall_1", "scan_s": 10, "watch_s": 120},
    "control.found": {
        "scan_id": "sc-0003",
        "local_id": "c1",
        "kind": "switch",
        "score": 0.91,
        "state": "off",
        "state_confidence": 0.93,
        "box": [0.42, 0.31, 0.51, 0.44],
        "panel_point": [0.12, 0.40],
        "fingerprint": FP,
        "crop": crop("cr-h-0121"),
        "spot_id": "living_wall_1",
    },
    "scan.done": {"scan_id": "sc-0003", "found": 3, "skipped": 1, "reason": None},
    "mapping.candidate": {
        "scan_id": "sc-0003",
        "local_id": "c1",
        "method": "watch",
        "control_state": {"before": "off", "after": "on"},
        "device_box": [0.70, 0.10, 0.88, 0.45],
        "before": crop("cr-h-0130"),
        "after": crop("cr-h-0131"),
        "change_score": 0.64,
        "device_type": "light",
        "device_score": 0.88,
        "spot_id": "living_wall_1",
        "room": "living_room",
    },
    "watch.set": {
        "enabled": True,
        "controls": [
            {
                "control_id": "ctl-0005",
                "kind": "switch",
                "fingerprint": FP,
                "seen_spot_id": "living_wall_1",
                "box": [0.42, 0.31, 0.51, 0.44],
            }
        ],
    },
    "control.used": {
        "control_id": "ctl-0005",
        "before": "off",
        "after": "on",
        "at": "2026-11-17T12:04:58.000Z",
        "confidence": 0.91,
    },
    "run.start": {
        "mapping_id": "mp-0002",
        "device": {"device_id": "dev_lamp_01", "name": "lamp", "risk": "low"},
        "action": "on",
        "decision": "act",
        "control": {
            "control_id": "ctl-0005",
            "kind": "switch",
            "fingerprint": FP,
            "panel_point": [0.12, 0.40],
            "press_spot_id": "living_wall_1",
        },
        "check": {"device_box": [0.70, 0.10, 0.88, 0.45], "spot_id": "living_wall_1", "device_type": "light"},
        "channels": [],
    },
    "run.progress": {
        "stage": "S6",
        "step": "gate",
        "status": "ok",
        "channel": "ARM",
        "detail": None,
        "latency_ms": 420,
    },
    "run.result": {
        "result": "success",
        "reason": None,
        "channel": "ARM",
        "checks": {"main": "yes", "second": None, "disagree": False},
        "stages": [
            {"stage": "S5", "status": "skipped", "latency_ms": 3, "reason": "no wireless channel"},
            {"stage": "S6", "status": "ok", "latency_ms": 6400, "reason": None},
        ],
        "attempts": [{"channel": "ARM", "latency_ms": 6400, "answer": "yes", "gate": "pass"}],
        "press_spot_id": "living_wall_1",
        "last_crop_id": None,
    },
    "stop": {"source": "app"},
    "hub.status": {
        "camera": "ok",
        "ral": {"impl": "script", "state": "idle"},
        "run_id": None,
        "queued": 0,
        "watching": 1,
    },
    "ack": {"ids": ["m-h-000412"]},
}
RUN_TYPES = {"run.start", "run.progress", "run.result"}


def envelope(type_, payload, run_id="r-0007"):
    return Envelope(
        type=type_,
        id="m-1",
        run_id=run_id if type_ in RUN_TYPES else None,
        sent_at=datetime(2026, 11, 17, tzinfo=UTC),
        payload=payload,
    )


@pytest.mark.parametrize("type_", sorted(EXAMPLES))
def test_wiki_examples_parse(type_):
    envelope(type_, EXAMPLES[type_]).body()


def test_every_message_type_has_an_example():
    from backend.common.messages import PAYLOADS

    assert set(PAYLOADS) == set(EXAMPLES)


def test_unknown_type_is_rejected():
    with pytest.raises(ValidationError):
        envelope("run.maybe", {})


def test_run_messages_need_run_id():
    with pytest.raises(ValidationError):
        Envelope(type="run.progress", id="m-1", run_id=None, sent_at=datetime.now(UTC), payload={})


@pytest.mark.fr("FR-07")
def test_arm_is_never_listed_in_run_start():
    bad = dict(EXAMPLES["run.start"], channels=[{"channel": "ARM", "success_score": 0.9, "latency_ms": 10}])
    with pytest.raises(ValidationError):
        RunStart.model_validate(bad)


@pytest.mark.fr("FR-12")
def test_run_result_needs_a_reason_unless_success():
    bad = dict(EXAMPLES["run.result"], result="cancelled", reason=None)
    with pytest.raises(ValidationError):
        RunResultMsg.model_validate(bad)


def test_crop_longest_side_at_most_256():
    bad = dict(EXAMPLES["control.found"], crop=dict(crop(), w=512))
    with pytest.raises(ValidationError):
        envelope("control.found", bad).body()


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        envelope("stop", {"source": "app", "frame": "..."}).body()
