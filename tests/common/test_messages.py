"""The hub messages of the wiki (contract version 2) parse, and the rules in their tables hold."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.common.messages import ANSWERS, PAYLOADS, RUN_TYPES, Check, Envelope, Gang, RefindResult
from tests.conftest import FP, sealed

BOX = [0.42, 0.31, 0.51, 0.44]
DEVICE_BOX = [0.70, 0.10, 0.88, 0.45]

EXAMPLES = {
    "recognize": {"purpose": "scan", "spot_id": "living_wall_1", "room": "living_room", "photo": sealed()},
    "recognize.result": {
        "controls": [
            {
                "kind": "switch",
                "box": BOX,
                "score": 0.91,
                "clear": True,
                "state": "off",
                "state_confidence": 0.93,
                "gang": {"count": 2, "index": 1},
                "link_guess": {"device_class": "ceiling_light", "p": 0.74},
                "control_id": None,
            }
        ],
        "person": False,
    },
    "check": {
        "purpose": "verify",
        "before": [sealed("ph_h0130")],
        "after": [sealed("ph_h0131")],
        "device_box": DEVICE_BOX,
        "device_type": "light",
        "action": "on",
    },
    "check.result": {
        "change": "off_to_on",
        "answer": "yes",
        "device_box": DEVICE_BOX,
        "device_type": "light",
        "confidence": 0.86,
        "reason": "the lamp region got brighter",
    },
    "refind": {
        "photo": sealed("ph_h0140"),
        "control": {"control_id": "ctl-0005", "kind": "switch", "box": BOX, "fingerprint": None},
        "actuators": [{"id": "A1", "faces": [0.0, 0.0, 0.5, 1.0]}],
    },
    "refind.result": {
        "found": True,
        "box": BOX,
        "plan": {"actuator": "A1", "push_ms": 600},
        "confidence": 0.9,
        "reason": "switch found near its saved place",
    },
    "people": {"photo": sealed("ph_h0141"), "work_area": [0.0, 0.0, 0.5, 1.0]},
    "people.result": {"clear": True, "confidence": 0.97, "reason": "no person in the work area"},
    "decide": {
        "trigger": "time",
        "device": {"type": "light", "risk": "low"},
        "action": "off",
        "mapping": {"status": "Verified", "confidence": 0.92},
        "routine": {"independence": "act_and_tell", "accept_share": 0.9},
        "confidence": 0.9,
    },
    "decide.result": {"decision": "act", "confidence": 0.9, "reason": "low risk and high confidence"},
    "app.request": {"method": "GET", "path": "/controls", "query": {}, "body": None, "session": "s3cr3t"},
    "app.response": {"status": 200, "body": {"items": []}},
    "app.event": {"kind": "run.progress", "data": {"stage": "S6", "step": "press", "status": "ok"}},
    "stop": {"source": "app"},
    "stop.done": {"ral": "stopped"},
    "hub.status": {"camera": "ok", "ral": {"impl": "script", "state": "idle"}, "run_id": None, "watching": 1},
}


def envelope(type_, payload, run_id="r-0007"):
    answer = type_ in ANSWERS.values()
    return Envelope(
        type=type_,
        id="m-1",
        re="m-0" if answer else None,
        run_id=run_id if type_ in RUN_TYPES else None,
        sent_at=datetime(2026, 11, 20, tzinfo=UTC),
        payload=payload,
    )


@pytest.mark.parametrize("type_", sorted(EXAMPLES))
def test_wiki_examples_parse(type_):
    envelope(type_, EXAMPLES[type_]).body()


def test_every_message_type_has_an_example():
    assert set(PAYLOADS) == set(EXAMPLES)


def test_every_request_has_an_answer_type():
    assert set(ANSWERS.values()) <= set(PAYLOADS)


def test_unknown_type_is_rejected():
    with pytest.raises(ValidationError):
        envelope("run.maybe", {})


def test_answers_need_re_and_requests_have_none():
    with pytest.raises(ValidationError):
        Envelope(type="decide.result", id="m-1", run_id=None, sent_at=datetime.now(UTC), payload={})
    with pytest.raises(ValidationError):
        Envelope(type="recognize", id="m-1", re="m-0", run_id=None, sent_at=datetime.now(UTC), payload={})


def test_refind_needs_run_id():
    with pytest.raises(ValidationError):
        Envelope(type="refind", id="m-1", run_id=None, sent_at=datetime.now(UTC), payload=EXAMPLES["refind"])


def test_decide_and_people_may_come_before_or_outside_a_run():
    """decide comes before the run is opened; people also guards a move order (FR-10)."""
    for type_ in ("decide", "people"):
        Envelope(type=type_, id="m-1", run_id=None, sent_at=datetime.now(UTC), payload=EXAMPLES[type_])


@pytest.mark.fr("FR-08")
def test_a_verify_check_names_the_device():
    bad = dict(EXAMPLES["check"], device_box=None)
    with pytest.raises(ValidationError):
        Check.model_validate(bad)


@pytest.mark.fr("FR-09")
def test_a_control_not_found_has_no_plan():
    bad = dict(EXAMPLES["refind.result"], found=False)
    with pytest.raises(ValidationError):
        RefindResult.model_validate(bad)


def test_a_watch_request_lists_the_known_controls():
    bad = dict(EXAMPLES["recognize"], purpose="watch")
    with pytest.raises(ValidationError):
        envelope("recognize", bad).body()


def test_gang_index_within_the_count():
    with pytest.raises(ValidationError):
        Gang(count=2, index=3)


def test_answers_need_a_reason():
    bad = dict(EXAMPLES["people.result"], reason="")
    with pytest.raises(ValidationError):
        envelope("people.result", bad).body()


def test_photo_longest_side_at_most_1280():
    bad = dict(EXAMPLES["people"], photo=dict(sealed(), w=4000))
    with pytest.raises(ValidationError):
        envelope("people", bad).body()


def test_fingerprint_has_384_numbers():
    ok = dict(EXAMPLES["refind"]["control"], fingerprint=FP)
    bad = dict(EXAMPLES["refind"]["control"], fingerprint=FP[:10])
    envelope("refind", dict(EXAMPLES["refind"], control=ok)).body()
    with pytest.raises(ValidationError):
        envelope("refind", dict(EXAMPLES["refind"], control=bad)).body()


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        envelope("stop", {"source": "app", "frame": "..."}).body()
