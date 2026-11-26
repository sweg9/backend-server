"""The orchestrator with the demo scenarios as its trigger source (FR-05 to FR-12, C-04, C-09)."""

import asyncio
import tempfile
from datetime import UTC, datetime

import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select

from backend.common.messages import AppEvent, AppRequest
from backend.common.runlog import RunEntry
from backend.common.vocab import Channel, HandOption
from backend.hub.app import AppHandlers
from backend.hub.db.models import Base, Control, Device, Mapping, OpenRun, Routine, Run, Scan, Situation
from backend.hub.db.session import make_engine, make_sessionmaker
from backend.hub.orchestrator import Orchestrator, SafeAnswers
from backend.hub.ral import make_ral
from backend.hub.runs import RESTARTED, end_left_open
from backend.hub.scenario import start
from backend.hub.seed import seed_demo

HASH = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("pw")


class Hub:
    """The hub's parts without the link: handlers, orchestrator and the events they send.

    The database is a file, as on the hub: with `:memory:` every session shares one connection, so two
    sessions writing at once (`database is locked`) would never show here."""

    def __init__(self, demo=True):
        self._dir = tempfile.TemporaryDirectory()
        engine = make_engine(f"sqlite:///{self._dir.name}/hub.db")
        Base.metadata.create_all(engine)
        self.db = make_sessionmaker(engine)
        with self.db() as s:
            seed_demo(s, "Asia/Seoul")
        self.events: list[tuple[str, dict]] = []

        async def emit(kind, data):
            AppEvent(kind=kind, data=data)  # as the link sends it: an unknown kind fails here, not on the hub
            self.events.append((kind, data))

        self.ral = make_ral(HandOption.SCRIPT)
        self.orch = Orchestrator(
            self.db,
            self.ral,
            {},
            SafeAnswers(),
            emit,
            time_zone="Asia/Seoul",
            effect_wait_s=3,
            unsure_recheck_s=3,
            actuator="A1",
        )
        self.app = AppHandlers(self.db, 30, HASH, emit, orch=self.orch, demo_scenarios=demo)
        self.session = None

    async def call(self, method, path, body=None, query=None):
        req = AppRequest(method=method, path=path, body=body, query=query or {}, session=self.session)
        r = await self.app.handle(req)
        if path == "/login":
            self.session = r.set_session
        return r

    async def play(self, name, speed=1000.0):
        await self.call("POST", "/login", {"password": "pw"})
        start(self.orch, name, speed)
        await self.orch.task

    def kinds(self):
        return {k for k, _ in self.events}

    def runs(self):
        with self.db() as s:
            return s.scalars(select(Run).order_by(Run.started_at)).all()


def plan_by_name(sit):
    return {i["name"]: i for i in sit["plan"]}


@pytest.mark.fr("FR-05")
@pytest.mark.fr("FR-07")
@pytest.mark.fr("FR-08")
@pytest.mark.fr("FR-09")
def test_leave_home_wireless_first_then_visits_and_presses():
    async def go():
        h = Hub()
        await h.play("leave_home")
        sits = (await h.call("GET", "/situations")).body
        sit = sits[0]
        assert sit["status"] == "needs_attention"
        p = plan_by_name(sit)
        assert (p["TV"]["method"], p["TV"]["result"]) == ("wireless", "success")
        assert p["Heater"]["result"] == "success"
        assert p["Air purifier"]["wireless"]["result"] == "failure" and p["Air purifier"]["method"] == "press"
        assert p["Bedroom ceiling light"]["wireless"]["reason"] == "the camera saw no change after the IR command"
        assert p["Living room ceiling light"]["wireless"]["result"] == "no_channel"
        assert p["Kitchen light"]["result"] == "unsure"
        visits = (await h.call("GET", "/visits")).body
        assert len(visits) == 4 and {v["reason"] for v in visits} == {"no_channel", "wireless_failed"}
        assert {"situation", "visits", "robot", "run.progress", "run.result", "notification"} <= h.kinds()
        assert (await h.call("GET", "/robot")).body["state"] == "idle"
        # every run entry keeps the rules: ARM after wireless (L-05), gate pass (L-04)
        runs = h.runs()
        assert len(runs) == 16
        for r in runs:
            RunEntry.model_validate(r.entry)
        presses = [c for c in h.ral.calls if c[0] == "press"]
        # one each, and a second for the bedroom light whose first press failed (FR-11); the unsure kitchen
        # light is looked at again, not pressed again
        assert len(presses) == 5
        assert p["Bedroom ceiling light"]["result"] == "success"
        bedroom = next(r for r in runs if r.run_id == p["Bedroom ceiling light"]["run_id"])
        assert [(a["channel"], a["answer"]) for a in bedroom.entry["attempts"]] == [
            ("IR", "no"),
            ("ARM", "no"),
            ("ARM", "yes"),
        ]
        kitchen = next(r for r in runs if r.run_id == p["Kitchen light"]["run_id"])
        assert [a["channel"] for a in kitchen.entry["attempts"]] == ["ARM"]
        return h, p

    asyncio.run(go())


@pytest.mark.fr("FR-11")
def test_the_resident_retries_the_unsure_run_once():
    async def go():
        h = Hub()
        await h.play("leave_home")
        kitchen = plan_by_name((await h.call("GET", "/situations")).body[0])["Kitchen light"]["run_id"]
        failed = [n for k, n in h.events if k == "notification" and n.get("run_id") == kitchen]
        assert failed and failed[0]["choices"] == ["retry", "leave"]
        h.orch.speed = 1000  # a scenario's speed ends with it; the retry is a real run
        r = await h.call("POST", f"/runs/{kitchen}/retry")
        assert r.status == 202
        await h.orch.task
        sit = (await h.call("GET", "/situations")).body[0]
        assert sit["status"] == "done"
        assert plan_by_name(sit)["Kitchen light"]["resolution"] == "retry"
        assert (await h.call("POST", f"/runs/{kitchen}/retry")).status == 409
        tv = plan_by_name(sit)["TV"]["run_id"]
        assert (await h.call("POST", f"/runs/{tv}/leave")).status == 409  # it worked

    asyncio.run(go())


@pytest.mark.fr("FR-06")
def test_ask_first_waits_for_the_answer_and_dismiss_does_nothing():
    async def go():
        h = Hub()
        with h.db() as s:
            s.scalar(select(Routine).where(Routine.time == "08:30")).independence = "ask_first"  # the heater
            s.commit()
        await h.call("POST", "/login", {"password": "pw"})
        start(h.orch, "leave_home", 1000)
        for _ in range(200):
            await asyncio.sleep(0.01)
            if h.orch.waiting:
                break
        sid = next(iter(h.orch.waiting))
        assert (await h.call("GET", f"/situations/{sid}")).body["status"] == "waiting_for_answer"
        assert (await h.call("POST", f"/situations/{sid}/dismiss")).status == 200
        await h.orch.task
        assert (await h.call("GET", f"/situations/{sid}")).body["status"] == "dismissed"
        assert len(h.runs()) == 10  # nothing ran
        assert (await h.call("POST", f"/situations/{sid}/approve")).status == 409

    asyncio.run(go())


@pytest.mark.fr("FR-06")
def test_paused_only_suggests():
    async def go():
        h = Hub()
        await h.call("POST", "/login", {"password": "pw"})
        await h.call("PUT", "/proactive", {"paused": True})
        await h.play("leave_home")
        assert (await h.call("GET", "/situations")).body[0]["status"] == "suggested"
        assert len(h.runs()) == 10

    asyncio.run(go())


@pytest.mark.fr("FR-10")
def test_the_stop_cancels_the_open_runs_and_the_situation():
    async def go():
        h = Hub()
        await h.call("POST", "/login", {"password": "pw"})
        start(h.orch, "leave_home", 5)
        for _ in range(500):
            await asyncio.sleep(0.01)
            if h.orch.open:
                break
        assert await h.orch.stop() == "stopped"
        assert ("stop",) in h.ral.calls
        sit = (await h.call("GET", "/situations")).body[0]
        assert sit["status"] == "cancelled"
        cancelled = [r for r in h.runs() if r.result == "cancelled" and r.entry["reason"] == "emergency stop"]
        assert cancelled
        with h.db() as s:
            assert s.scalar(select(func.count()).select_from(OpenRun)) == 0
        assert (await h.call("GET", "/robot")).body["state"] == "stopped"

    asyncio.run(go())


@pytest.mark.fr("FR-12")
def test_a_task_that_dies_stops_the_hand_cancels_its_runs_and_tells_the_resident(caplog):
    """C-04: an error inside a task is not only a line in the log; the hand stops as after a stop."""

    async def go():
        h = Hub()

        async def broken_press(actuator, ms):
            raise RuntimeError("serial port gone")

        working_press, h.ral.press = h.ral.press, broken_press
        await h.play("leave_home")
        assert ("stop",) in h.ral.calls
        why = "the hub hit an error (RuntimeError)"
        assert any(r.result == "cancelled" and r.entry["reason"] == why for r in h.runs())
        with h.db() as s:
            assert s.scalar(select(func.count()).select_from(OpenRun)) == 0
        sit = (await h.call("GET", "/situations")).body[0]
        assert sit["status"] == "cancelled"
        told = [d for k, d in h.events if k == "notification" and d["topic"] == "task_failed"]
        assert len(told) == 1 and told[0]["level"] == "warning" and told[0]["task"] == "leave_home"
        assert (await h.call("GET", "/robot")).body["state"] == "stopped"
        # the next task starts as after a stop
        h.ral.press = working_press
        await h.play("one_press")
        assert (await h.call("GET", "/robot")).body["state"] == "idle"

    with caplog.at_level("INFO", logger="backend.hub.orchestrator"):
        asyncio.run(go())
    text = caplog.text
    assert "task leave_home failed" in text and "notify task_failed" in text
    assert "cancelled: the hub hit an error" in text


@pytest.mark.fr("FR-02")
def test_new_control_fixes_the_wrong_guess_and_maps_the_strip_with_caution():
    async def go():
        h = Hub()
        await h.play("new_control")
        fixed = [d for k, d in h.events if k == "mapping" and d.get("previous_device_id")]
        assert len(fixed) == 1
        with h.db() as s:
            m = s.get(Mapping, fixed[0]["mapping_id"])
            assert m.status == "Verified" and m.device_id != fixed[0]["previous_device_id"]
            assert s.scalar(select(func.count()).select_from(Control)) == 10
            assert s.scalar(select(Scan)).done_at is not None
            assert s.scalar(select(Routine).where(Routine.mapping_id == m.mapping_id)) is None  # Hyunseo's flow
            new_strips = select(Mapping).join(Control).where(Control.kind == "power_strip", Mapping.status == "Pending")
            strip = s.scalar(new_strips)
            assert strip.device_id is None  # C-08: never pressed until a person confirms
        assert "scan" in h.kinds()

    asyncio.run(go())


def test_scenarios_are_off_unless_the_hub_runs_with_them():
    async def go():
        h = Hub(demo=False)
        await h.call("POST", "/login", {"password": "pw"})
        assert (await h.call("GET", "/scenarios")).status == 404
        assert (await h.call("POST", "/scenarios/leave_home/start")).status == 404
        h2 = Hub()
        await h2.call("POST", "/login", {"password": "pw"})
        assert len((await h2.call("GET", "/scenarios")).body) == 3
        assert (await h2.call("POST", "/scenarios/nope/start")).status == 404
        assert (await h2.call("POST", "/scenarios/leave_home/start", query={"speed": "99"})).status == 422

    asyncio.run(go())


@pytest.mark.fr("FR-12")
def test_runs_left_open_by_a_restart_end_as_cancelled():
    h = Hub()
    with h.db() as s:
        m = s.scalar(select(Mapping).where(Mapping.status == "Verified"))
        s.add(
            OpenRun(
                run_id="r_left",
                trigger="time",
                routine_id=None,
                device_id=m.device_id,
                mapping_id=m.mapping_id,
                action="off",
                decision="act",
                confidence=0.9,
                risk="low",
                phase="running",
                started_at=datetime.now(UTC),
                dedup_key="x",
            )
        )
        s.commit()
        assert end_left_open(s, "Asia/Seoul") == 1
        run = s.get(Run, "r_left")
        assert run.result == "cancelled" and run.entry["reason"] == RESTARTED
        assert s.scalar(select(func.count()).select_from(Situation)) == 0


def test_without_scenario_answers_the_hub_only_suggests():
    async def go():
        h = Hub()
        with h.db() as s:
            dev = s.scalar(select(Mapping).where(Mapping.status == "Verified")).device_id
        h.orch.speed = 1000
        sit = await h.orch.handle_situation("test", "sensor", "A test.", [(dev, "off")], paused=False)
        assert sit["status"] == "suggested"
        assert Channel.ARM not in [c[0] for c in h.ral.calls]

    asyncio.run(go())


# --- API 0.5.0: the dashboard's proposal merged ---------------------------------------------------


async def kitchen_failed(h):
    await h.play("leave_home")
    h.orch.speed = 1000
    return plan_by_name((await h.call("GET", "/situations")).body[0])["Kitchen light"]["run_id"]


@pytest.mark.fr("FR-11")
@pytest.mark.fr("FR-12")
def test_retry_answers_the_new_run_and_the_choice_stays_with_the_runs():
    async def go():
        h = Hub()
        kitchen = await kitchen_failed(h)
        r = await h.call("POST", f"/runs/{kitchen}/retry")
        assert r.status == 202 and r.body["choice"] == "retry"
        new = r.body["retry_run_id"]
        await h.orch.task
        runs = {x["run_id"]: x for x in (await h.call("GET", "/runs")).body}
        assert runs[kitchen]["resolution"] == {"choice": "retry", "retry_run_id": new}
        assert runs[new]["retry_of"] == kitchen and runs[new]["resolution"] is None
        assert runs[new]["situation_id"] == runs[kitchen]["situation_id"] is not None
        result = [d for k, d in h.events if k == "run.result" and d["run_id"] == new]
        assert result and result[0]["retry_of"] == kitchen
        assert "because you asked me to try again" in result[0]["explanation"]  # C-07: not "your routine"
        # the choice is in the database, not only in the orchestrator's memory: a restarted hub still refuses
        h.orch.situations.clear()
        assert (await h.call("POST", f"/runs/{kitchen}/leave")).body["code"] == "already_chosen"

    asyncio.run(go())


@pytest.mark.fr("FR-11")
def test_retry_without_a_verified_mapping_is_refused_at_once():
    async def go():
        h = Hub()
        kitchen = await kitchen_failed(h)
        with h.db() as s:
            s.scalar(select(Mapping).where(Mapping.device_id == s.get(Run, kitchen).device_id)).status = "Rejected"
            s.commit()
        r = await h.call("POST", f"/runs/{kitchen}/retry")
        assert (r.status, r.body["code"]) == (409, "not_verified")
        assert not h.orch.busy
        assert (await h.call("POST", f"/runs/{kitchen}/leave")).status == 200  # nothing was chosen yet

    asyncio.run(go())


@pytest.mark.fr("FR-08")
@pytest.mark.fr("FR-10")
def test_run_progress_has_step_codes_and_outcomes_before_the_robot_event():
    async def go():
        h = Hub()
        await h.play("leave_home")
        progress = [d for k, d in h.events if k == "run.progress"]
        assert {p["step"] for p in progress} == {"decide", "wireless", "queued", "move", "gate", "press", "check"}
        assert {p["wireless"] for p in progress if "wireless" in p} == {"success", "failure", "no_channel"}
        assert {p["gate"] for p in progress if p["step"] == "gate" and "gate" in p} == {"pass"}
        assert {"yes", "unsure"} <= {p["answer"] for p in progress if p["step"] == "check" and "answer" in p}
        assert all(p["text"] for p in progress)
        # the robot never shows a newer step than the run
        for n, (k, d) in enumerate(h.events):
            if k == "robot" and d["task"] and d["task"]["run_id"]:
                before = [p for kk, p in h.events[:n] if kk == "run.progress" and p["run_id"] == d["task"]["run_id"]]
                assert before and before[-1]["step"] == d["task"]["step"]

    asyncio.run(go())


@pytest.mark.fr("FR-06")
def test_a_high_risk_device_gets_a_notification_on_every_run():
    async def go():
        h = Hub()
        await h.play("leave_home")
        heater = [r.run_id for r in h.runs() if r.entry["risk"] == "high" and r.situation_id]
        assert heater
        told = {n["run_id"] for k, n in h.events if k == "notification" and n.get("run_id")}
        assert set(heater) <= told

    asyncio.run(go())


@pytest.mark.fr("FR-06")
def test_the_proactive_setting_can_be_read():
    async def go():
        h = Hub()
        await h.call("POST", "/login", {"password": "pw"})
        assert (await h.call("GET", "/proactive")).body == {"paused": False}
        await h.call("PUT", "/proactive", {"paused": True})
        assert (await h.call("GET", "/proactive")).body == {"paused": True}

    asyncio.run(go())


def scripted(h):
    from backend.hub.scenario import ScriptedAnswers

    h.orch.answers = ScriptedAnswers()
    h.orch.speed = 1000


@pytest.mark.fr("FR-09")
@pytest.mark.fr("FR-10")
def test_a_move_order_goes_through_its_stops_and_presses_nothing():
    async def go():
        h = Hub()
        await h.call("POST", "/login", {"password": "pw"})
        scripted(h)
        with h.db() as s:
            m = s.scalar(select(Mapping).where(Mapping.status == "Verified"))
            device, spot = m.device_id, s.get(Control, m.control_id).press_spot_id
        r = await h.call("POST", "/robot/route", {"stops": [device, "dock"]})
        assert r.status == 202 and r.body["stops"] == [spot, "dock"]
        await h.orch.task
        route = [d for k, d in h.events if k == "robot.route"]
        assert route[0]["state"] == "moving" and route[-1] == {
            "route_id": r.body["route_id"],
            "stops": [spot, "dock"],
            "reached": 2,
            "state": "done",
            "reason": None,
        }
        assert not [c for c in h.ral.calls if c[0] == "press"]
        assert (await h.call("GET", "/robot")).body["spot_id"] == "dock"
        assert (await h.call("POST", "/robot/route", {"stops": ["nowhere"]})).status == 404
        with h.db() as s:
            s.get(Mapping, m.mapping_id).status = "Stale"
            s.commit()
        assert (await h.call("POST", "/robot/route", {"stops": [device]})).body["code"] == "not_verified"

    asyncio.run(go())


@pytest.mark.fr("FR-10")
def test_a_move_order_waits_for_the_gate_and_the_stop_cancels_it():
    async def go():
        h = Hub()
        await h.call("POST", "/login", {"password": "pw"})
        h.orch.speed = 1000  # SafeAnswers: no gate answer, so no movement (C-04)
        r = await h.call("POST", "/robot/route", {"stops": ["dock", "kitchen_wall_1"]})
        await h.orch.task
        last = [d for k, d in h.events if k == "robot.route"][-1]
        assert (last["state"], last["reached"]) == ("cancelled", 0) and "safety gate" in last["reason"]
        assert ("go_to_spot", "kitchen_wall_1") not in h.ral.calls

        scripted(h)
        h.orch.speed = 1
        r = await h.call("POST", "/robot/route", {"stops": ["kitchen_wall_1"]})
        for _ in range(100):
            await asyncio.sleep(0.01)
            if h.orch.robot.state == "moving":
                break
        await h.orch.stop()
        last = [d for k, d in h.events if k == "robot.route" and d["route_id"] == r.body["route_id"]][-1]
        assert (last["state"], last["reason"]) == ("cancelled", "emergency stop")

    asyncio.run(go())


# --- FR-11: the second press ----------------------------------------------------------------------


class PressAnswers:
    """Act, no wireless, and the given check answers and gates for the presses, in order."""

    def __init__(self, checks, gates=None):
        from backend.hub.scenario import ScriptedAnswers

        self.base = ScriptedAnswers()
        self.checks = list(checks)
        self.gates = list(gates or [])

    async def decide(self, item, paused, trigger):
        return await self.base.decide(item, paused, trigger)

    async def look_before(self, device_id, channel):
        pass

    async def verify(self, device_id, channel, action, sent):
        return self.checks.pop(0)

    async def gate(self, device_id):
        return self.gates.pop(0) if self.gates else (True, None)

    async def route_gate(self, spot_id):
        return True, None


async def press_once_more(h, answers):
    h.orch.answers = answers
    h.orch.speed = 1000
    with h.db() as s:
        dev = s.scalar(select(Device).where(Device.name == "Living room ceiling light")).device_id  # no channel
    sit = await h.orch.handle_situation("test", "sensor", "A test.", [(dev, "off")], paused=False)
    run = h.runs()[-1]
    RunEntry.model_validate(run.entry)
    presses = [c for c in h.ral.calls if c[0] == "press"]
    return sit, run, presses


@pytest.mark.fr("FR-11")
def test_a_failed_check_leads_to_one_more_press_and_no_third():
    async def go():
        h = Hub()
        no = ("no", "the camera saw no change")
        sit, run, presses = await press_once_more(h, PressAnswers([no, no]))
        assert len(presses) == 2
        assert run.result == "failure" and run.entry["reason"].startswith("pressed twice")
        assert [(a["channel"], a["gate"]) for a in run.entry["attempts"]] == [("ARM", "pass"), ("ARM", "pass")]
        gates = [d for k, d in h.events if k == "run.progress" and d["step"] == "gate" and "gate" in d]
        assert [g["gate"] for g in gates] == ["pass", "pass"]  # a new gate before each press (C-04)
        assert sit["status"] == "needs_attention"

    asyncio.run(go())


@pytest.mark.fr("FR-11")
@pytest.mark.fr("FR-10")
def test_a_failed_gate_before_the_second_press_cancels_without_pressing():
    async def go():
        h = Hub()
        answers = PressAnswers(
            [("no", "the camera saw no change")], gates=[(True, None), (False, "a person came near")]
        )
        _, run, presses = await press_once_more(h, answers)
        assert len(presses) == 1
        assert (run.result, run.entry["reason"]) == ("cancelled", "a person came near")
        assert [a["channel"] for a in run.entry["attempts"]] == ["ARM"]

    asyncio.run(go())


@pytest.mark.fr("FR-11")
@pytest.mark.fr("FR-08")
def test_unsure_after_a_failed_press_is_never_pressed_a_third_time():
    async def go():
        h = Hub()
        answers = PressAnswers([("no", "no change"), ("unsure", "dark"), ("unsure", "still dark")])
        _, run, presses = await press_once_more(h, answers)
        assert len(presses) == 2
        assert run.result == "unsure"
        assert [a["answer"] for a in run.entry["attempts"]] == ["no", "unsure"]

    asyncio.run(go())


@pytest.mark.fr("FR-11")
@pytest.mark.fr("FR-08")
def test_unsure_then_no_on_the_second_look_is_not_pressed_again():
    async def go():
        h = Hub()
        answers = PressAnswers([("unsure", "dark"), ("no", "no change")])
        _, run, presses = await press_once_more(h, answers)
        assert len(presses) == 1  # C-09: an unsure check never leads to another press
        assert run.result == "failure" and not run.entry["reason"].startswith("pressed twice")
        texts = [d["text"] for k, d in h.events if k == "run.progress" and d["step"] == "check"]
        assert "it did not work: press once more" not in texts

    asyncio.run(go())


# --- demo scenario 3 as shown: one press --------------------------------------------------------


@pytest.mark.fr("FR-07")
@pytest.mark.fr("FR-10")
def test_one_press_turns_off_tv_and_heater_by_wifi_and_presses_one_light_once():
    async def go():
        h = Hub()
        await h.play("one_press")
        sit = (await h.call("GET", "/situations")).body[0]
        p = plan_by_name(sit)
        assert set(p) == {"TV", "Heater", "Living room ceiling light"}  # scenario 1 did not run
        assert (p["TV"]["method"], p["TV"]["result"]) == ("wireless", "success")
        assert (p["Heater"]["method"], p["Heater"]["result"]) == ("wireless", "success")
        light = p["Living room ceiling light"]
        assert (light["method"], light["result"]) == ("press", "success")
        assert light["decision"] == "act" and sit["status"] != "waiting_for_answer"
        assert len([c for c in h.ral.calls if c[0] == "press"]) == 1
        gates = [d for k, d in h.events if k == "run.progress" and d["step"] == "gate" and "gate" in d]
        assert [g["gate"] for g in gates] == ["pass"]  # C-04
        assert (await h.call("GET", "/robot")).body["state"] == "idle"

    asyncio.run(go())


@pytest.mark.fr("FR-02")
def test_one_press_after_scenario_1_presses_the_stand_lamp_without_asking():
    """Scenario 1 makes the stand lamp Verified; the demo shows no question."""

    async def go():
        h = Hub()
        await h.play("new_control")
        await h.play("one_press")
        sit = (await h.call("GET", "/situations")).body[0]
        p = plan_by_name(sit)
        assert "Living room stand lamp" in p and "Living room ceiling light" not in p
        assert p["Living room stand lamp"]["result"] == "success"
        assert not [n for k, n in h.events if k == "notification" and n.get("topic") == "question"]

    asyncio.run(go())
