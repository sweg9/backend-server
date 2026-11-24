"""The orchestrator with the demo scenarios as its trigger source (FR-05 to FR-12, C-04, C-09)."""

import asyncio
from datetime import UTC, datetime

import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select

from backend.common.messages import AppRequest
from backend.common.runlog import RunEntry
from backend.common.vocab import Channel, HandOption
from backend.hub.app import AppHandlers
from backend.hub.db.models import Base, Control, Mapping, OpenRun, Routine, Run, Scan, Situation
from backend.hub.db.session import make_engine, make_sessionmaker
from backend.hub.orchestrator import Orchestrator, SafeAnswers
from backend.hub.ral import make_ral
from backend.hub.runs import RESTARTED, end_left_open
from backend.hub.scenario import start
from backend.hub.seed import seed_demo

HASH = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("pw")


class Hub:
    """The hub's parts without the link: handlers, orchestrator and the events they send."""

    def __init__(self, demo=True):
        engine = make_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = make_sessionmaker(engine)
        with self.db() as s:
            seed_demo(s, "Asia/Seoul")
        self.events: list[tuple[str, dict]] = []

        async def emit(kind, data):
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
        assert len(presses) == 4  # one each; the unsure kitchen light is looked at again, not pressed again
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


@pytest.mark.fr("FR-02")
def test_new_control_fixes_the_wrong_guess_and_adds_a_habit():
    async def go():
        h = Hub()
        await h.play("new_control")
        fixed = [d for k, d in h.events if k == "mapping" and d.get("previous_device_id")]
        assert len(fixed) == 1
        with h.db() as s:
            m = s.get(Mapping, fixed[0]["mapping_id"])
            assert m.status == "Verified" and m.device_id != fixed[0]["previous_device_id"]
            assert s.scalar(select(func.count()).select_from(Control)) == 9
            assert s.scalar(select(Scan)).done_at is not None
            assert s.scalar(select(Routine).where(Routine.mapping_id == m.mapping_id)).source == "habit"
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
        assert len((await h2.call("GET", "/scenarios")).body) == 2
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
