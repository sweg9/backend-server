"""Scenario judge: plays the demo scenarios (1 `new_control`, 3 `one_press`; also the long `leave_home`)
through the public API, as the Dashboard would, and says pass or fail with the numbers for the slides
(wiki: Demo scenarios).

`backend judge --url http://127.0.0.1:8000` signs in (password from JUDGE_PASSWORD or a prompt), opens
the live socket `/ws/app`, starts each scenario and records every `app.event` with the time it came. The
resident's part is played too: a question is approved, and a run left unsure is retried once (FR-11).
Then the recorded events are judged:

- the scenario's story happened (which device went which way, what the check found);
- the safety rules held in every run: a gate pass right before every press (C-04), at most 2 presses
  (FR-11), no press after an unsure check (FR-08), wireless before the press when there is a channel (C-09);
- the numbers: trigger to first press, press to the check's answer, the check success rate, the
  scenario's length.

It works the same against a laptop (`hand: script`) and the demo rig with the real hand. Times are from
the judge's clock; with `--speed` other than 1 the waits of the scenario are shortened, so only numbers
from `--speed 1` go on the slides. The judge sees no photo and keeps no secret: the password is never
printed or written.
"""

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from statistics import mean, median
from typing import Any

# --- what a scenario must show ---------------------------------------------------------------------

# Demo scenario 3 (leave_home): how each device should end, the way it got there, and its presses.
# Other devices may be in the plan too: after scenario 1 the stand lamp has a Verified mapping, so it is
# turned off as well (by a press, as it has no channel). They are held to the safety rules, not listed here.
LEAVE_HOME = {
    "TV": {"result": "success", "method": "wireless", "presses": 0},
    "Heater": {"result": "success", "method": "wireless", "presses": 0},
    "Air purifier": {"result": "success", "method": "press", "presses": 1},  # no Bluetooth answer
    "Bedroom ceiling light": {"result": "success", "method": "press", "presses": 2},  # IR no effect, FR-11
    "Living room ceiling light": {"result": "success", "method": "press", "presses": 1},  # no channel
    "Kitchen light": {"result": "unsure", "method": "press", "presses": 1, "retry": "success"},  # FR-08
}


@dataclass
class Event:
    t: float  # seconds since the scenario was started
    kind: str
    data: dict[str, Any]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    scenario: str
    speed: float
    checks: list[Check] = field(default_factory=list)
    numbers: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, bool(ok), detail))


def _first(events: list[Event], kind: str, **match: Any) -> Event | None:
    return next((e for e in events if e.kind == kind and all(e.data.get(k) == v for k, v in match.items())), None)


def _robot_back(events: list[Event]) -> bool:
    robots = [e.data for e in events if e.kind == "robot"]
    return bool(robots) and robots[-1].get("state") == "idle" and robots[-1].get("task") is None


def _seconds(x: float | None) -> float | None:
    return None if x is None else round(x, 2)


# --- the safety rules, run by run ------------------------------------------------------------------


@dataclass
class RunSteps:
    run_id: str
    device_id: str
    steps: list[Event] = field(default_factory=list)

    @property
    def presses(self) -> list[Event]:
        return [e for e in self.steps if e.data.get("step") == "press"]

    def answers_after(self, press: Event) -> list[str]:
        """The check answers between this press and the next one."""
        later = [e for e in self.steps if e.t >= press.t and e is not press]
        out = []
        for e in later:
            if e.data.get("step") == "press":
                break
            if e.data.get("step") == "check" and e.data.get("answer"):
                out.append(e.data["answer"])
        return out


def runs_of(events: list[Event]) -> dict[str, RunSteps]:
    runs: dict[str, RunSteps] = {}
    for e in events:
        if e.kind == "run.progress" and e.data.get("run_id"):
            r = runs.setdefault(e.data["run_id"], RunSteps(e.data["run_id"], e.data["device_id"]))
            r.steps.append(e)
    return runs


def safety_problems(r: RunSteps) -> list[str]:
    """Breaks of C-04, FR-08, FR-11 and C-09 in one run's steps; empty when it kept them all."""
    problems = []
    gate_ok = False
    unsure_seen = False
    for e in r.steps:
        step = e.data.get("step")
        if step == "gate" and "gate" in e.data:
            gate_ok = e.data["gate"] == "pass"
        elif step == "press":
            if not gate_ok:
                problems.append("a press without a gate pass right before it (C-04)")
            if unsure_seen:
                problems.append("a press after an unsure check (FR-08)")
            gate_ok = False  # the next press needs a new gate (FR-11)
        elif step == "check" and e.data.get("answer") == "unsure":
            unsure_seen = True
    if len(r.presses) > 2:
        problems.append(f"{len(r.presses)} presses (FR-11: at most 2)")
    methods = [e.data.get("method") for e in r.steps if e.data.get("method")]
    if "wireless" in methods and "press" in methods and methods.index("press") < methods.index("wireless"):
        problems.append("a press before wireless was tried (C-09)")
    return problems


# --- demo scenario 3 -------------------------------------------------------------------------------


def judge_leave_home(events: list[Event], situation: dict[str, Any] | None, speed: float = 1.0) -> Report:
    rep = Report("leave_home", speed)
    if situation is None:
        rep.check("a situation came in", False, "no situation event")
        return rep
    plan = {i["name"]: i for i in situation.get("plan", [])}
    runs = runs_of(events)
    rep.check("a situation came in", True, situation.get("summary", ""))

    names = {i["device_id"]: i["name"] for i in plan.values()}
    results = {e.data.get("run_id"): e.data.get("result") for e in events if e.kind == "run.result"}
    by_device: dict[str, list[RunSteps]] = {}
    for r in runs.values():  # in the order the runs began: the scenario's run, then the resident's retry
        by_device.setdefault(r.device_id, []).append(r)
    for name, want in LEAVE_HOME.items():
        item = plan.get(name)
        if item is None:
            rep.check(f"{name}: in the plan", False, "missing")
            continue
        device_runs = by_device.get(item["device_id"], [])
        first = device_runs[0] if device_runs else None
        presses = len(first.presses) if first else 0
        got = (
            results.get(first.run_id) if first else None,
            "press" if presses else "wireless",
            presses,
        )
        rep.check(
            f"{name}: {want['result']} by {want['method']}, {want['presses']} press(es)",
            got == (want["result"], want["method"], want["presses"]),
            f"got {got[0]} by {got[1]}, {got[2]} press(es)",
        )
        if "retry" in want:
            retry = device_runs[1] if len(device_runs) > 1 else None
            rep.check(
                f"{name}: the resident's retry ends {want['retry']}",
                retry is not None and results.get(retry.run_id) == want["retry"],
                "no retry run" if retry is None else f"got {results.get(retry.run_id)}",
            )

    problems = {rid: safety_problems(r) for rid, r in runs.items()}
    bad = {names.get(runs[rid].device_id, rid): p for rid, p in problems.items() if p}
    rep.check("safety rules in every run (C-04, FR-08, FR-11, C-09)", not bad, json.dumps(bad) if bad else "")
    rep.check("the robot is back at the dock", _robot_back(events))

    # The numbers.
    start = _first(events, "situation")
    t0 = start.t if start else 0.0
    all_presses = sorted((p for r in runs.values() for p in r.presses), key=lambda e: e.t)
    to_answer, answers = [], []
    for r in runs.values():
        for p in r.presses:
            after = [e for e in r.steps if e.t > p.t and e.data.get("step") == "check" and e.data.get("answer")]
            ans = r.answers_after(p)
            if after and ans:
                final = [e for e in after if e.data["answer"] == ans[-1]][0]
                to_answer.append(final.t - p.t)
                answers.append(ans[-1])
    wireless = [e.data["wireless"] for e in events if e.kind == "run.progress" and e.data.get("wireless")]
    rep.numbers = {
        "trigger_to_first_press_s": _seconds(all_presses[0].t - t0 if all_presses else None),
        "trigger_to_robot_back_s": _seconds(
            max((e.t for e in events if e.kind == "robot"), default=t0) - t0 if events else None
        ),
        "presses": len(all_presses),
        "press_to_check_answer_s": {
            "median": _seconds(median(to_answer)) if to_answer else None,
            "max": _seconds(max(to_answer)) if to_answer else None,
        },
        "press_checks": {a: answers.count(a) for a in ("yes", "no", "unsure")},
        "press_check_success_rate": round(answers.count("yes") / len(answers), 2) if answers else None,
        "wireless": {w: wireless.count(w) for w in sorted(set(wireless))},
        "runs": len(runs),
    }
    return rep


# --- demo scenario 3 as shown: one press ----------------------------------------------------------

ONE_PRESS_LIGHTS = ("Living room stand lamp", "Living room ceiling light")  # the stand lamp after scenario 1


def judge_one_press(events: list[Event], situation: dict[str, Any] | None, speed: float = 1.0) -> Report:
    rep = Report("one_press", speed)
    if situation is None:
        rep.check("a situation came in", False, "no situation event")
        return rep
    rep.check("a situation came in", True, situation.get("summary", ""))
    plan = {i["name"]: i for i in situation.get("plan", [])}
    runs = runs_of(events)
    results = {e.data.get("run_id"): e.data.get("result") for e in events if e.kind == "run.result"}
    for name in ("TV", "Heater"):
        item = plan.get(name, {})
        r = runs.get(item.get("run_id") or "")
        rep.check(
            f"{name}: off over wireless, no press",
            results.get(item.get("run_id")) == "success" and r is not None and not r.presses,
            f"got {results.get(item.get('run_id'))}",
        )
    light = next((plan[n] for n in ONE_PRESS_LIGHTS if n in plan), None)
    r = runs.get((light or {}).get("run_id") or "")
    rep.check(
        "one light: no channel, one press, the check says it worked",
        light is not None and r is not None and len(r.presses) == 1 and results.get(r.run_id) == "success",
        "no light in the plan" if light is None else f"{light['name']}: {results.get(light.get('run_id'))}",
    )
    all_presses = [p for x in runs.values() for p in x.presses]
    rep.check("exactly one press in the whole scenario", len(all_presses) == 1, f"{len(all_presses)} presses")
    rep.check("no question to the resident", _first(events, "notification", topic="question") is None)
    bad = {rid: p for rid, p in ((rid, safety_problems(x)) for rid, x in runs.items()) if p}
    rep.check("safety rules in every run (C-04, FR-08, FR-11, C-09)", not bad, json.dumps(bad) if bad else "")
    rep.check("the robot is back at the dock", _robot_back(events))

    start = _first(events, "situation")
    t0 = start.t if start else 0.0
    press = all_presses[0] if all_presses else None
    answer = None
    if press is not None and r is not None:
        answer = next(
            (e for e in r.steps if e.t > press.t and e.data.get("step") == "check" and e.data.get("answer")), None
        )
    rep.numbers = {
        "trigger_to_press_s": _seconds(press.t - t0 if press else None),
        "press_to_check_answer_s": _seconds(answer.t - press.t if press and answer else None),
        "trigger_to_robot_back_s": _seconds(max((e.t for e in events if e.kind == "robot"), default=t0) - t0),
        "check_answer": answer.data.get("answer") if answer else None,
    }
    return rep


# --- demo scenario 1 -------------------------------------------------------------------------------


def judge_new_control(events: list[Event], devices: dict[str, str], speed: float = 1.0) -> Report:
    rep = Report("new_control", speed)
    by_name = {v: k for k, v in devices.items()}
    ceiling, stand = by_name.get("Living room ceiling light"), by_name.get("Living room stand lamp")

    started = _first(events, "scan", status="started")
    done = _first(events, "scan", status="done")
    checked = _first(events, "scan", status="checked")
    verified = next(
        (
            e
            for e in events
            if e.kind == "mapping" and e.data.get("status") == "Verified" and "previous_device_id" in e.data
        ),
        None,
    )
    routine = next((e for e in events if e.kind == "routine"), None)
    flip = _first(events, "notification", topic="flip_request")

    rep.check("the scan started and finished", started is not None and done is not None)
    rep.check(
        "the scan found 2 controls, 1 of them new (FR-01)",
        done is not None and (done.data.get("found"), done.data.get("new")) == (2, 1),
        "" if done is None else f"found {done.data.get('found')}, new {done.data.get('new')}",
    )
    pending = next((e for e in events if e.kind == "mapping" and e.data.get("status") == "Pending"), None)
    rep.check(
        "a Pending mapping to the guess, the ceiling light (FR-02)",
        pending is not None and pending.data.get("device_id") == ceiling,
    )
    rep.check("the resident was asked to flip the switch", flip is not None)
    rep.check(
        "the check saw the stand lamp change, not the guess",
        checked is not None
        and checked.data.get("guess_right") is False
        and checked.data.get("check", {}).get("device_id") == stand,
    )
    rep.check(
        "the mapping was fixed to the stand lamp and Verified (FR-02, C-08)",
        verified is not None
        and verified.data.get("device_id") == stand
        and verified.data.get("previous_device_id") == ceiling,
    )
    rep.check(
        "a habit routine was added that asks first (FR-04)",
        routine is not None and routine.data.get("independence") == "ask_first",
        "" if routine is None else f"{routine.data.get('action')} at {routine.data.get('time')}",
    )
    rep.check("the robot is back at the dock", _robot_back(events))

    t0 = started.t if started else 0.0
    rep.numbers = {
        "scan_s": _seconds(done.t - started.t if started and done else None),
        "scan_start_to_verified_s": _seconds(verified.t - t0 if verified else None),
        "flip_request_to_verified_s": _seconds(verified.t - flip.t if verified and flip else None),
        "detections_shown": sum(len(e.data.get("detections", [])) for e in events if e.kind == "scan"),
    }
    return rep


# --- playing a scenario through the API ------------------------------------------------------------


class JudgeError(Exception):
    pass


async def play(url: str, password: str, name: str, speed: float, timeout_s: float, retry: bool = True):
    """Starts one scenario and records its events until the robot is back with nothing left to answer.
    Returns (events, devices, situation)."""
    import httpx
    from websockets.asyncio.client import connect

    base = url.rstrip("/")
    async with httpx.AsyncClient(base_url=base, timeout=10) as http:
        r = await http.post("/login", json={"password": password})
        if not r.is_success:
            raise JudgeError(f"sign-in failed with {r.status_code}")
        session = r.cookies.get("session") or _cookie(r.headers.get("set-cookie", ""))
        headers = {"Cookie": f"session={session}"}  # the cookie is Secure; send it by hand on plain http
        devices = {d["device_id"]: d["name"] for d in (await http.get("/devices", headers=headers)).json()}
        ws_url = "ws" + base.removeprefix("http") + "/ws/app"
        events: list[Event] = []
        async with connect(ws_url, additional_headers=headers) as ws:
            r = await http.post(f"/scenarios/{name}/start", params={"speed": speed}, headers=headers)
            if r.status_code != 202:
                raise JudgeError(f"{name} did not start: {r.status_code} {r.text}")
            t0 = time.monotonic()
            await _record(ws, http, headers, events, t0, timeout_s, retry)
        situation = None
        sit = _first(events, "situation")
        if sit is not None:
            situation = (await http.get(f"/situations/{sit.data['situation_id']}", headers=headers)).json()
    return events, devices, situation


def _cookie(header: str) -> str | None:
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "session":
            return v
    return None


async def _record(ws, http, headers, events: list[Event], t0: float, timeout_s: float, retry: bool) -> None:
    """The resident's part: approve each question; retry each unsure run once the robot is back."""
    moved = False
    to_retry: list[str] = []
    retried: set[str] = set()
    deadline = t0 + timeout_s
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise JudgeError(f"the scenario did not finish within {timeout_s} s")
        try:
            raw = await asyncio.wait_for(ws.recv(), left)
        except TimeoutError:
            continue
        msg = json.loads(raw)
        e = Event(round(time.monotonic() - t0, 3), msg.get("kind", ""), msg.get("data") or {})
        events.append(e)
        if e.kind == "notification" and e.data.get("topic") == "question" and e.data.get("situation_id"):
            await http.post(f"/situations/{e.data['situation_id']}/approve", headers=headers)
        if e.kind == "notification" and e.data.get("choices") == ["retry", "leave"] and retry:
            run_id = e.data.get("run_id")
            if run_id and run_id not in retried:
                to_retry.append(run_id)
        if e.kind == "robot":
            if e.data.get("state") != "idle":
                moved = True
            elif moved and e.data.get("task") is None:
                if to_retry:
                    run_id = to_retry.pop(0)
                    r = await http.post(f"/runs/{run_id}/retry", headers=headers)
                    if r.status_code == 409:  # still busy: try at the next idle
                        to_retry.insert(0, run_id)
                    else:
                        retried.add(run_id)
                        moved = False
                else:
                    return


# --- output ----------------------------------------------------------------------------------------


def text(rep: Report) -> str:
    lines = [f"## {rep.scenario}  (speed {rep.speed:g})  {'PASS' if rep.passed else 'FAIL'}", ""]
    for c in rep.checks:
        lines.append(f"  [{'ok' if c.ok else 'FAIL'}] {c.name}" + (f"  ({c.detail})" if c.detail else ""))
    lines.append("")
    for k, v in rep.numbers.items():
        lines.append(f"  {k}: {v}")
    if rep.speed != 1:
        lines.append("  (times are shortened by --speed; only --speed 1 numbers go on the slides)")
    return "\n".join(lines)


def summary(reports: list[Report]) -> dict[str, Any]:
    return {
        "passed": all(r.passed for r in reports),
        "reports": [asdict(r) | {"passed": r.passed} for r in reports],
    }


def aggregate(reports: list[Report]) -> dict[str, Any]:
    """Over several rounds of leave_home: the numbers the slides use."""
    op = [r for r in reports if r.scenario == "one_press"]
    to_press = [r.numbers["trigger_to_press_s"] for r in op if r.numbers.get("trigger_to_press_s") is not None]
    answers = [r.numbers.get("check_answer") for r in op if r.numbers.get("check_answer")]
    lh = [r for r in reports if r.scenario == "leave_home"]
    rates = [r.numbers["press_check_success_rate"] for r in lh if r.numbers.get("press_check_success_rate") is not None]
    return {
        "reports": len(reports),
        "passed": sum(r.passed for r in reports),
        "one_press_trigger_to_press_s_mean": _seconds(mean(to_press)) if to_press else None,
        "one_press_check_success_rate": round(answers.count("yes") / len(answers), 2) if answers else None,
        "leave_home_press_check_success_rate_mean": round(mean(rates), 2) if rates else None,
    }


async def judge(url: str, password: str, names: list[str], speed: float, rounds: int, timeout_s: float):
    reports = []
    for _ in range(rounds):
        for name in names:
            events, devices, situation = await play(url, password, name, speed, timeout_s)
            if name == "leave_home":
                reports.append(judge_leave_home(events, situation, speed))
            elif name == "one_press":
                reports.append(judge_one_press(events, situation, speed))
            else:
                reports.append(judge_new_control(events, devices, speed))
    return reports
