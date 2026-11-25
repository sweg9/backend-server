"""The scenario judge on the events of the real scenarios, played in the hub (tests/hub/test_scenarios.py
Hub), and on event streams that break a rule (C-04, FR-08, FR-11, C-09)."""

import asyncio
import time

import pytest

from backend.tools.judge import Event, RunSteps, judge_leave_home, judge_new_control, safety_problems
from tests.hub.test_scenarios import Hub


async def recorded(name, retry=False):
    h = Hub()
    t0 = time.monotonic()
    events: list[Event] = []
    original = h.orch.emit

    async def emit(kind, data):
        events.append(Event(time.monotonic() - t0, kind, dict(data)))
        await original(kind, data)

    h.orch.emit = emit
    await h.play(name)
    if retry:
        unsure = [e.data["run_id"] for e in events if e.data.get("choices") == ["retry", "leave"]]
        h.orch.speed = 1000
        for run_id in unsure:
            assert (await h.call("POST", f"/runs/{run_id}/retry")).status == 202
            await h.orch.task
    sits = (await h.call("GET", "/situations")).body
    devices = {d["device_id"]: d["name"] for d in (await h.call("GET", "/devices")).body}
    return events, (sits[0] if sits else None), devices


@pytest.mark.fr("FR-11")
def test_leave_home_with_the_residents_retry_passes():
    events, sit, _ = asyncio.run(recorded("leave_home", retry=True))
    rep = judge_leave_home(events, sit, 1000)
    assert rep.passed, [c for c in rep.checks if not c.ok]
    n = rep.numbers
    assert n["presses"] == 6  # 5 in the scenario, 1 in the retry
    # purifier, bedroom (2nd press), living room, kitchen retry: yes; bedroom 1st press: no; kitchen: unsure
    assert n["press_checks"] == {"yes": 4, "no": 1, "unsure": 1}
    assert n["press_check_success_rate"] == pytest.approx(4 / 6, abs=0.01)
    assert n["trigger_to_first_press_s"] is not None


def test_leave_home_without_the_retry_fails_only_the_retry_check():
    events, sit, _ = asyncio.run(recorded("leave_home"))
    failed = [c.name for c in judge_leave_home(events, sit).checks if not c.ok]
    assert failed == ["Kitchen light: the resident's retry ends success"]


@pytest.mark.fr("FR-02")
def test_new_control_passes():
    events, _, devices = asyncio.run(recorded("new_control"))
    rep = judge_new_control(events, devices, 1000)
    assert rep.passed, [c for c in rep.checks if not c.ok]
    assert rep.numbers["scan_s"] is not None and rep.numbers["detections_shown"] > 0


def test_no_situation_fails():
    assert not judge_leave_home([], None).passed


# --- the safety rules on made-up steps -------------------------------------------------------------


def steps(*items):
    r = RunSteps("r_1", "dev_1")
    for i, (step, extra) in enumerate(items):
        r.steps.append(Event(float(i), "run.progress", {"run_id": "r_1", "device_id": "dev_1", "step": step, **extra}))
    return r


GATE_PASS = ("gate", {"gate": "pass", "method": "press"})
PRESS = ("press", {"method": "press"})


@pytest.mark.fr("FR-10")
def test_a_press_without_a_gate_is_caught():
    assert "C-04" in safety_problems(steps(PRESS))[0]


@pytest.mark.fr("FR-11")
def test_a_second_press_needs_a_new_gate():
    problems = safety_problems(steps(GATE_PASS, PRESS, ("check", {"answer": "no"}), PRESS))
    assert any("C-04" in p for p in problems)


@pytest.mark.fr("FR-11")
def test_three_presses_are_caught():
    r = steps(*[x for _ in range(3) for x in (GATE_PASS, PRESS, ("check", {"answer": "no"}))])
    assert any("FR-11" in p for p in safety_problems(r))


@pytest.mark.fr("FR-08")
def test_a_press_after_unsure_is_caught():
    r = steps(GATE_PASS, PRESS, ("check", {"answer": "unsure"}), GATE_PASS, PRESS)
    assert any("FR-08" in p for p in safety_problems(r))


def test_a_press_before_wireless_is_caught():
    r = steps(GATE_PASS, PRESS, ("wireless", {"method": "wireless"}))
    assert any("C-09" in p for p in safety_problems(r))


def test_a_good_run_has_no_problems():
    r = steps(("wireless", {"method": "wireless"}), GATE_PASS, PRESS, ("check", {"answer": "yes"}))
    assert safety_problems(r) == []
