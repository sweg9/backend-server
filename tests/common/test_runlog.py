"""The run entry of execution-log.md and rules L-04, L-05, L-09."""

import pytest
from pydantic import ValidationError

from backend.common.runlog import RunEntry

EXAMPLE = {
    "run_id": "r-0007",
    "trigger": "routine",
    "started_at": "2026-11-03T12:05:00Z",
    "intent": "turn on the lamp",
    "device_id": "dev_lamp_01",
    "risk": "low",
    "confidence": 0.86,
    "decision": "act",
    "channel": "ARM",
    "result": "success",
    "reason": None,
    "checks": {"source": "camera", "main": "yes", "second": {"room_brighter": "yes"}, "disagree": False},
    "stages": [
        {"stage": "S4", "status": "ok", "latency_ms": 2100},
        {"stage": "S5", "status": "fail", "latency_ms": 5200, "reason": "IR sent, lamp did not light"},
        {"stage": "S6", "status": "ok", "latency_ms": 6400},
    ],
    "attempts": [
        {"channel": "IR", "latency_ms": 5200, "answer": "no", "source": "camera"},
        {"channel": "ARM", "latency_ms": 6400, "answer": "yes", "source": "camera", "gate": "pass"},
    ],
    "explanation": "I switched on the lamp at 21:05 by pressing its switch because of your evening routine.",
}


def entry(**changes):
    return RunEntry.model_validate({**EXAMPLE, **changes})


@pytest.mark.fr("FR-12")
def test_execution_log_example_parses():
    entry()


@pytest.mark.fr("FR-10")
def test_arm_without_gate_pass_is_rejected():
    with pytest.raises(ValidationError, match="L-04"):
        entry(attempts=[{"channel": "ARM", "latency_ms": 1, "answer": "yes", "gate": "person in work area"}])


@pytest.mark.fr("FR-11")
def test_at_most_two_arm_attempts():
    arm = {"channel": "ARM", "latency_ms": 1, "answer": "no", "gate": "pass"}
    with pytest.raises(ValidationError, match="L-04"):
        entry(attempts=[arm, arm, arm], result="failure", reason="still off")


@pytest.mark.fr("FR-07")
def test_wireless_after_arm_is_rejected():
    with pytest.raises(ValidationError, match="L-05"):
        entry(
            channel="IR",
            attempts=[
                {"channel": "ARM", "latency_ms": 1, "answer": "no", "gate": "pass"},
                {"channel": "IR", "latency_ms": 1, "answer": "yes"},
            ],
        )


@pytest.mark.fr("FR-08")
def test_wifi_result_comes_from_the_platform():
    with pytest.raises(ValidationError, match="L-09"):
        entry(channel="WIFI", attempts=[{"channel": "WIFI", "latency_ms": 1, "answer": "yes", "source": "camera"}])
