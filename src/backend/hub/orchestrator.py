"""Runs what a trigger asks for (FR-05 to FR-12, C-09): the plan, the wireless channels in order, the
visit order for what wireless could not do, the press at each spot, and the run entry for each device.
Every step goes to the app as `app.event`.

Where the answers come from is behind `Answers`: the decision (the enclave's `decide`), the effect check
(the platform's report or the camera's `check`) and the safety gate (`people`). The real answers go over
the link; a scenario (scenario.py) gives fixed ones. Rules kept whatever the answers are:

- Wireless first, then the press, and only after wireless failed or when there is none (C-09, L-05).
- The press only after the gate passed right before it (C-04).
- A failed check after a press leads to one more press after a new gate, at most 2 presses (FR-11, L-04).
- `unsure` is looked at once more without pressing again, then stays unsure; the resident chooses retry
  or leave (FR-08, FR-11).
- The emergency stop turns the hand off first, then cancels everything open, a move order too (FR-10).
- A move order passes the safety gate before every leg (C-04) and presses nothing.
- A high-risk device gets a notification after every run, whatever the result (C-03).
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.common.ids import new_id
from backend.common.runlog import MAX_ARM_ATTEMPTS, RunEntry
from backend.common.vocab import (
    Channel,
    CheckAnswer,
    Decision,
    DeviceAction,
    MappingStatus,
    Risk,
    RunResult,
    Stage,
    Trigger,
)
from backend.hub.channels.base import ChannelAdapter, SendResult
from backend.hub.db.models import ChannelRow, Control, Device, Mapping, OpenRun, Routine, Run, Situation
from backend.hub.explain import explain, in_sentence
from backend.hub.ral.base import RobotAbstractionLayer
from backend.hub.runs import write_run
from backend.hub.views import iso, run_view, situation_view

log = logging.getLogger(__name__)
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

HOME_SPOT = "dock"
PUSH_MS = 400  # how long an actuator pushes; tuned with the real hand
ACTIVE = ("received", "planning", "waiting_for_answer", "running")
EMERGENCY = "emergency stop"


class Busy(Exception):
    pass


class Step(StrEnum):
    """A run's steps in `run.progress` and the robot's task (API 0.5.0). S4: decide. S5: wireless.
    S6: queued, move, gate, press, check."""

    DECIDE = "decide"
    WIRELESS = "wireless"
    QUEUED = "queued"
    MOVE = "move"
    GATE = "gate"
    PRESS = "press"
    CHECK = "check"


class Answers(Protocol):
    async def decide(self, item: dict[str, Any], paused: bool) -> Decision: ...

    async def verify(
        self, device_id: str, channel: Channel, action: DeviceAction, sent: SendResult | None
    ) -> tuple[CheckAnswer, str | None]: ...

    async def gate(self, device_id: str) -> tuple[bool, str | None]: ...

    async def route_gate(self, spot_id: str) -> tuple[bool, str | None]: ...


class SafeAnswers:
    """Until the link's real answers are built: what the hub does when an answer does not come in time
    (wiki: Hub messages > Answers that do not come). Only suggest, unsure, and no press."""

    async def decide(self, item: dict[str, Any], paused: bool) -> Decision:
        return Decision.SUGGEST

    async def verify(self, device_id, channel, action, sent) -> tuple[CheckAnswer, str | None]:
        return CheckAnswer.UNSURE, "no check answer"

    async def gate(self, device_id: str) -> tuple[bool, str | None]:
        return False, "no answer from the safety gate"

    async def route_gate(self, spot_id: str) -> tuple[bool, str | None]:
        return False, "no answer from the safety gate"


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Robot:
    impl: str
    state: str = "idle"
    spot_id: str = HOME_SPOT
    task: dict[str, Any] | None = None
    plan: list[str] = field(default_factory=list)
    ral_state: str = "idle"

    def view(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "spot_id": self.spot_id,
            "task": self.task,
            "plan": list(self.plan),
            "ral": {"impl": self.impl, "state": self.ral_state},
        }


@dataclass
class RunCtx:
    """One run while it goes on; becomes a RunEntry when it ends."""

    run_id: str
    trigger: Trigger
    device_id: str
    device_name: str
    risk: str
    mapping_id: str | None
    control_id: str | None
    press_spot: str | None
    action: DeviceAction
    confidence: float
    decision: Decision
    routine_id: str | None
    routine_time: str | None
    situation_id: str | None
    retry_of: str | None = None
    started_at: datetime = field(default_factory=now)
    stages: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        db: sessionmaker,
        ral: RobotAbstractionLayer,
        adapters: dict[Channel, ChannelAdapter],
        answers: Answers,
        emit: Emit,
        *,
        time_zone: str,
        effect_wait_s: float,
        unsure_recheck_s: float,
        actuator: str,
    ):
        self.db = db
        self.ral = ral
        self.adapters = adapters
        self.answers = answers
        self.emit = emit
        self.time_zone = time_zone
        self.effect_wait_s = effect_wait_s
        self.unsure_recheck_s = unsure_recheck_s
        self.actuator = actuator
        self.speed = 1.0  # a scenario may play faster; real runs keep 1
        self.robot = Robot(impl=str(ral.impl))
        self.visits: list[dict[str, Any]] = []
        self.situations: dict[str, dict[str, Any]] = {}  # the ones still going on
        self.open: dict[str, RunCtx] = {}
        self.waiting: dict[str, asyncio.Future[bool]] = {}
        self.route: dict[str, Any] | None = None  # the move order going on, as in `robot.route`
        self.task: asyncio.Task | None = None
        self.task_name: str | None = None

    # --- the one task at a time ----------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def start(self, name: str, work: Callable[[], Awaitable[None]]) -> None:
        if self.busy:
            raise Busy(f"the robot is busy with {self.task_name}")
        if self.robot.state == "stopped":  # a new task after the stop: the hand was reset by the resident
            self.robot.state, self.robot.ral_state = "idle", "idle"
            reset = getattr(self.ral, "reset", None)
            if reset is not None:
                reset()

        async def run() -> None:
            try:
                await work()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("task %s failed", name)

        self.task_name = name
        self.task = asyncio.create_task(run(), name=name)

    async def wait(self, seconds: float) -> None:
        await asyncio.sleep(seconds / self.speed)

    # --- events --------------------------------------------------------------------------------

    async def notify(self, topic: str, message: str, level: str = "info", **extra: Any) -> None:
        await self.emit("notification", {"level": level, "topic": topic, "message": message, "at": iso(now()), **extra})

    async def robot_event(self) -> None:
        await self.emit("robot", self.robot.view())

    async def visits_event(self) -> None:
        await self.emit("visits", {"visits": self.visits})

    async def progress(
        self, ctx: RunCtx, step: Step, text: str, stage: Stage, channel: Channel | None = None, **outcome: Any
    ) -> None:
        """One step of one run (`run.progress`). `outcome` holds what applies: `wireless` (success, failure,
        no_channel), `gate` (pass, fail), `answer` (yes, no, unsure) and `reason`."""
        method = None if channel is None else ("press" if channel == Channel.ARM else "wireless")
        await self.emit(
            "run.progress",
            {
                "run_id": ctx.run_id,
                "situation_id": ctx.situation_id,
                "device_id": ctx.device_id,
                "stage": stage,
                "step": step,
                "text": text,
                "method": method,
                "channel": channel,
                **outcome,
            },
        )

    async def set_task(
        self, step: Step, text: str, stage: Stage | None = None, ctx: RunCtx | None = None, channel=None
    ) -> None:
        """What the robot does now; for a run, its `run.progress` goes first so the robot never shows a
        newer step than the run."""
        method = None if channel is None else ("press" if channel == Channel.ARM else "wireless")
        self.robot.task = {
            "run_id": ctx.run_id if ctx else None,
            "device_id": ctx.device_id if ctx else None,
            "stage": stage,
            "step": step,
            "text": text,
            "method": method,
            "channel": channel,
        }
        if ctx is not None:
            await self.progress(ctx, step, text, stage, channel)
        await self.robot_event()

    async def move_to(self, spot: str) -> None:
        if self.robot.spot_id == spot:
            return
        self.robot.state, self.robot.ral_state = "moving", "busy"
        await self.robot_event()
        await self.ral.go_to_spot(spot)  # in the prototype a team member pushes the cart
        await self.wait(2.0)
        self.robot.spot_id = spot
        await self.robot_event()

    async def go_home(self) -> None:
        self.robot.task, self.robot.plan = None, []
        await self.move_to(HOME_SPOT)
        self.robot.state, self.robot.ral_state = "idle", "idle"
        await self.robot_event()

    # --- situations ----------------------------------------------------------------------------

    async def save_situation(self, sit: dict[str, Any]) -> None:
        with self.db() as db:
            row = db.get(Situation, sit["situation_id"])
            if row is None:
                db.add(
                    Situation(
                        situation_id=sit["situation_id"],
                        kind=sit["kind"],
                        trigger=sit["trigger"],
                        summary=sit["summary"],
                        status=sit["status"],
                        plan=sit["plan"],
                        received_at=datetime.fromisoformat(sit["received_at"].replace("Z", "+00:00")),
                        updated_at=now(),
                    )
                )
            else:
                row.status, row.plan, row.updated_at = sit["status"], list(sit["plan"]), now()
            db.commit()
        if sit["status"] in ACTIVE or sit["status"] == "needs_attention":
            self.situations[sit["situation_id"]] = sit
        else:
            self.situations.pop(sit["situation_id"], None)
        await self.emit("situation", sit)

    def plan_item(self, device_id: str, action: DeviceAction) -> dict[str, Any] | None:
        """The device with its Verified mapping, channels and routine; None without a Verified mapping (C-08)."""
        with self.db() as db:
            d = db.get(Device, device_id)
            m = db.scalar(
                select(Mapping).where(Mapping.device_id == device_id, Mapping.status == MappingStatus.VERIFIED)
            )
            if d is None or m is None:
                return None
            channels = db.scalars(
                select(ChannelRow.channel)
                .where(ChannelRow.device_id == device_id)
                .order_by(ChannelRow.success_score.desc())
            ).all()
            r = db.scalar(
                select(Routine).where(
                    Routine.mapping_id == m.mapping_id, Routine.action == action, Routine.removed_at.is_(None)
                )
            )
            return {
                "device_id": d.device_id,
                "name": d.name,
                "room": d.room,
                "risk": d.risk,
                "mapping_id": m.mapping_id,
                "routine_id": r.routine_id if r else None,
                "independence": r.independence if r else None,
                "action": action,
                "decision": None,
                "confidence": m.confidence,
                "method": "wireless" if channels else "press",
                "wireless": {"channel": channels[0], "result": "pending"}
                if channels
                else {"channel": None, "result": "no_channel"},
                "run_id": None,
                "result": None,
                "reason": None,
                "resolution": None,
            }

    async def handle_situation(
        self, kind: str, trigger: Trigger, summary: str, wants: list[tuple[str, DeviceAction]], paused: bool
    ) -> dict[str, Any]:
        sit = {
            "situation_id": new_id("sit"),
            "kind": kind,
            "trigger": trigger,
            "summary": summary,
            "received_at": iso(now()),
            "status": "received",
            "plan": [],
        }
        await self.save_situation(sit)
        await self.notify("situation", summary, situation_id=sit["situation_id"])
        await self.wait(1.5)

        # 1. The plan, device by device, so the screen can show it growing.
        sit["status"] = "planning"
        await self.save_situation(sit)
        for device_id, action in wants:
            item = self.plan_item(device_id, action)
            if item is None:
                continue
            item["decision"] = await self.answers.decide(item, paused)
            item.pop("independence")
            sit["plan"].append(item)
            await self.save_situation(sit)
            await self.wait(0.6)
        items = sit["plan"]
        if not items or all(i["decision"] == Decision.SUGGEST for i in items):
            sit["status"] = "suggested"
            await self.save_situation(sit)
            await self.notify("suggestion", "I only suggest this time: " + summary, situation_id=sit["situation_id"])
            return sit
        if any(i["decision"] == Decision.ASK for i in items):
            sit["status"] = "waiting_for_answer"
            await self.save_situation(sit)
            fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            self.waiting[sit["situation_id"]] = fut
            await self.notify(
                "question",
                "Shall I go ahead? " + summary,
                situation_id=sit["situation_id"],
                choices=["approve", "dismiss"],
            )
            try:
                approved = await fut
            finally:
                self.waiting.pop(sit["situation_id"], None)
            if not approved:
                sit["status"] = "dismissed"
                await self.save_situation(sit)
                return sit
            for i in items:
                if i["decision"] == Decision.ASK:
                    i["decision"] = Decision.ACT

        # 2. Wireless first, in the order of the device's channels (C-09).
        sit["status"] = "running"
        act = [i for i in items if i["decision"] == Decision.ACT]
        ctxs = {i["device_id"]: self.open_run(trigger, i, sit["situation_id"]) for i in act}
        for i in act:
            i["run_id"] = ctxs[i["device_id"]].run_id
        await self.save_situation(sit)
        for i in act:
            await self.progress(ctxs[i["device_id"]], Step.DECIDE, "decided to act", Stage.S4)
        for i in act:
            ch = i["wireless"]["channel"]
            if ch is None:
                continue
            ctx = ctxs[i["device_id"]]
            answer, why = await self.send_wireless(ctx, Channel(ch))
            i["wireless"]["result"] = "success" if answer == CheckAnswer.YES else "failure"
            await self.progress(
                ctx,
                Step.WIRELESS,
                f"{ch}: " + ("it worked" if answer == CheckAnswer.YES else "it did not work"),
                Stage.S5,
                Channel(ch),
                wireless=i["wireless"]["result"],
                answer=answer,
                reason=None if answer == CheckAnswer.YES else why,
            )
            if answer == CheckAnswer.YES:
                await self.close(ctx, RunResult.SUCCESS)
            else:
                i["wireless"]["reason"] = why
                i["method"] = "press"
                await self.save_situation(sit)

        # 3. The visit order for what wireless could not do; then the robot goes on its own.
        todo = [ctxs[i["device_id"]] for i in act if i["result"] is None]
        if todo:
            by_spot: dict[str, list[RunCtx]] = {}
            for ctx in todo:
                by_spot.setdefault(ctx.press_spot or HOME_SPOT, []).append(ctx)
            for spot, group in by_spot.items():
                for ctx in group:
                    item = next(i for i in act if i["device_id"] == ctx.device_id)
                    no_channel = item["wireless"]["result"] == "no_channel"
                    if no_channel:
                        await self.progress(ctx, Step.WIRELESS, "no wireless channel", Stage.S5, wireless="no_channel")
                    self.add_visit(ctx, spot, "no_channel" if no_channel else "wireless_failed")
                    await self.progress(ctx, Step.QUEUED, "waits for the robot", Stage.S6, Channel.ARM)
            self.robot.plan = [v["spot_id"] for v in self.visits if v["status"] == "waiting"]
            await self.visits_event()
            await self.robot_event()
            await self.wait(1.0)
            for v in [v for v in self.visits if v["situation_id"] == sit["situation_id"] and v["status"] == "waiting"]:
                await self.press_visit(v, self.open[v["run_id"]])
            await self.go_home()

        sit["status"] = self.situation_end(sit)
        await self.save_situation(sit)
        return sit

    @staticmethod
    def situation_end(sit: dict[str, Any]) -> str:
        settled = all(i["result"] in (RunResult.SUCCESS, None) or i["resolution"] == "leave" for i in sit["plan"])
        return "done" if settled else "needs_attention"

    def answer(self, situation_id: str, yes: bool) -> bool:
        fut = self.waiting.get(situation_id)
        if fut is None or fut.done():
            return False
        fut.set_result(yes)
        return True

    # --- one run -------------------------------------------------------------------------------

    def open_run(
        self, trigger: Trigger, item: dict[str, Any], situation_id: str | None, retry_of: str | None = None
    ) -> RunCtx:
        with self.db() as db:
            m = db.get(Mapping, item["mapping_id"])
            c = db.get(Control, m.control_id) if m else None
            r = db.get(Routine, item["routine_id"]) if item.get("routine_id") else None
            ctx = RunCtx(
                run_id=new_id("r"),
                trigger=trigger,
                device_id=item["device_id"],
                device_name=item["name"],
                risk=item["risk"],
                mapping_id=item["mapping_id"],
                control_id=c.control_id if c else None,
                press_spot=c.press_spot_id if c else None,
                action=DeviceAction(item["action"]),
                confidence=item["confidence"],
                decision=Decision(item["decision"]),
                routine_id=item.get("routine_id"),
                routine_time=r.time if r else None,
                situation_id=situation_id,
                retry_of=retry_of,
            )
            ctx.stages.append({"stage": Stage.S4, "status": "ok", "latency_ms": 900})
            db.add(
                OpenRun(
                    run_id=ctx.run_id,
                    trigger=trigger,
                    routine_id=ctx.routine_id,
                    device_id=ctx.device_id,
                    mapping_id=ctx.mapping_id,
                    action=ctx.action,
                    decision=ctx.decision,
                    confidence=ctx.confidence,
                    risk=ctx.risk,
                    phase="running",
                    started_at=ctx.started_at,
                    dedup_key=f"{ctx.device_id}:{ctx.action}",
                    situation_id=situation_id,
                )
            )
            db.commit()
        self.open[ctx.run_id] = ctx
        return ctx

    def attempt(self, ctx: RunCtx, channel: Channel, answer: CheckAnswer, latency_ms: int, reason: str | None) -> None:
        arm = channel == Channel.ARM
        a = {
            "channel": channel,
            "latency_ms": latency_ms,
            "answer": answer,
            "source": "platform" if channel in (Channel.WIFI, Channel.BT) else "camera",
        }
        if arm:
            a["gate"] = "pass"
        ctx.attempts.append(a)
        ctx.stages.append(
            {
                "stage": Stage.S6 if arm else Stage.S5,
                "status": "ok" if answer == CheckAnswer.YES else "fail",
                "latency_ms": latency_ms,
                "reason": reason,
            }
        )

    async def send_wireless(self, ctx: RunCtx, channel: Channel) -> tuple[CheckAnswer, str | None]:
        await self.set_task(Step.WIRELESS, f"send over {channel}", Stage.S5, ctx, channel)
        t = now()
        adapter = self.adapters.get(channel)
        sent = await adapter.send(ctx.device_id, None, ctx.action) if adapter else None
        await self.wait(1.2)
        if sent is None or not sent.sent:
            answer, why = CheckAnswer.NO, (sent.reason if sent and sent.reason else f"no answer over {channel}")
        else:
            answer, why = await self.answers.verify(ctx.device_id, channel, ctx.action, sent)
        self.attempt(ctx, channel, answer, int((now() - t).total_seconds() * 1000), why)
        return answer, why

    def add_visit(self, ctx: RunCtx, spot: str, reason: str) -> dict[str, Any]:
        v = {
            "visit_id": new_id("vis"),
            "order": sum(1 for x in self.visits if x["status"] == "waiting") + 1,
            "situation_id": ctx.situation_id,
            "spot_id": spot,
            "control_id": ctx.control_id,
            "device_id": ctx.device_id,
            "run_id": ctx.run_id,
            "reason": reason,
            "status": "waiting",
        }
        self.visits = [x for x in self.visits if x["status"] in ("waiting", "moving", "pressing", "checking")]
        self.visits.append(v)
        return v

    async def press_visit(self, v: dict[str, Any], ctx: RunCtx) -> None:
        async def status(s: str) -> None:
            v["status"] = s
            await self.visits_event()

        await status("moving")
        await self.set_task(Step.MOVE, "drive to the switch", Stage.S6, ctx, Channel.ARM)
        await self.move_to(v["spot_id"])
        if self.robot.plan and self.robot.plan[0] == v["spot_id"]:
            self.robot.plan.pop(0)
        # FR-11: a failed check (`no`) leads to one more press, after a new gate; at most MAX_ARM_ATTEMPTS
        # presses (L-04). `unsure` is looked at once more and never pressed again (FR-08).
        for n in range(1, MAX_ARM_ATTEMPTS + 1):
            again = n > 1
            await status("pressing")
            self.robot.state = "pressing"
            await self.set_task(
                Step.GATE,
                "safety gate before the second press" if again else "safety gate: no person in the work area",
                Stage.S6,
                ctx,
                Channel.ARM,
            )
            ok, why = await self.answers.gate(ctx.device_id)
            await self.wait(1.0)
            await self.progress(
                ctx,
                Step.GATE,
                "safety gate passed" if ok else f"safety gate failed: {why}",
                Stage.S6,
                Channel.ARM,
                gate="pass" if ok else "fail",
                reason=None if ok else why,
            )
            if not ok:  # C-04: no press
                ctx.stages.append({"stage": Stage.S6, "status": "skipped", "latency_ms": 0, "reason": why})
                await self.close(ctx, RunResult.CANCELLED, why)
                await status("cancelled")
                return
            t = now()
            await self.set_task(Step.PRESS, "press again" if again else "press", Stage.S6, ctx, Channel.ARM)
            await self.ral.press(self.actuator, PUSH_MS)
            await self.ral.release(self.actuator)
            self.robot.state = "checking"
            await status("checking")
            await self.set_task(Step.CHECK, "check the effect with the camera", Stage.S6, ctx, Channel.ARM)
            await self.wait(self.effect_wait_s)
            answer, why = await self.answers.verify(ctx.device_id, Channel.ARM, ctx.action, None)
            if answer == CheckAnswer.UNSURE:  # FR-08: one more look, never another press
                await self.progress(ctx, Step.CHECK, "unsure", Stage.S6, Channel.ARM, answer=answer, reason=why)
                await self.set_task(
                    Step.CHECK, "unsure: look once more without pressing again", Stage.S6, ctx, Channel.ARM
                )
                await self.wait(self.unsure_recheck_s)
                answer, why = await self.answers.verify(ctx.device_id, Channel.ARM, ctx.action, None)
            more = answer == CheckAnswer.NO and n < MAX_ARM_ATTEMPTS
            await self.progress(
                ctx,
                Step.CHECK,
                {
                    CheckAnswer.YES: "it worked",
                    CheckAnswer.NO: "it did not work: press once more" if more else "it did not work",
                }.get(answer, "still unsure"),
                Stage.S6,
                Channel.ARM,
                answer=answer,
                reason=None if answer == CheckAnswer.YES else why,
            )
            self.attempt(ctx, Channel.ARM, answer, int((now() - t).total_seconds() * 1000), why)
            if not more:
                break
        if answer == CheckAnswer.NO and n > 1:
            why = f"pressed twice: {why or 'the check saw no change'}"
        result = {CheckAnswer.YES: RunResult.SUCCESS, CheckAnswer.NO: RunResult.FAILURE}.get(answer, RunResult.UNSURE)
        await self.close(ctx, result, why)
        await status("done" if result == RunResult.SUCCESS else "failed")

    async def close(self, ctx: RunCtx, result: RunResult, reason: str | None = None) -> None:
        if result != RunResult.SUCCESS and not reason:
            reason = {RunResult.FAILURE: "the check saw no change", RunResult.UNSURE: "the check could not tell"}.get(
                result, EMERGENCY
            )
        ctx.stages.append({"stage": Stage.S7, "status": "ok", "latency_ms": 120})
        last = ctx.attempts[-1] if ctx.attempts else None
        channel = Channel(last["channel"]) if last else None
        entry = RunEntry(
            run_id=ctx.run_id,
            trigger=ctx.trigger,
            started_at=ctx.started_at,
            intent=f"turn {ctx.action} the {in_sentence(ctx.device_name)}",
            device_id=ctx.device_id,
            risk=ctx.risk,
            confidence=ctx.confidence,
            decision=ctx.decision,
            channel=channel,
            result=result,
            reason=None if result == RunResult.SUCCESS else reason,
            checks={
                "source": last["source"] if last else None,
                "main": last["answer"] if last else None,
                "second": None,
                "disagree": False,
            },
            stages=ctx.stages,
            attempts=ctx.attempts,
            explanation=explain(
                device_name=ctx.device_name,
                action=ctx.action,
                trigger=ctx.trigger,
                started_at=ctx.started_at,
                time_zone=self.time_zone,
                result=result,
                channel=channel,
                reason=reason,
                routine_time=ctx.routine_time,
                retried=ctx.retry_of is not None,
            ),
        )
        with self.db() as db:
            row = write_run(
                db,
                entry,
                ended_at=now(),
                routine_id=ctx.routine_id,
                mapping_id=ctx.mapping_id,
                situation_id=ctx.situation_id,
            )
            if result == RunResult.SUCCESS and ctx.control_id:
                c = db.get(Control, ctx.control_id)
                c.state, c.seen_at = ctx.action, now()
            db.commit()
            view = run_view(row, retry_of=ctx.retry_of)
        self.open.pop(ctx.run_id, None)
        sit = self.situations.get(ctx.situation_id or "")
        if sit is not None:
            for i in sit["plan"]:
                if i["run_id"] == ctx.run_id:
                    i["result"], i["reason"] = result, entry.reason
            await self.save_situation(sit)
        await self.emit("run.result", view)
        if result in (RunResult.FAILURE, RunResult.UNSURE):
            await self.notify(
                "run_failed",
                entry.explanation,
                level="warning",
                run_id=ctx.run_id,
                device_id=ctx.device_id,
                situation_id=ctx.situation_id,
                choices=["retry", "leave"],
            )
        elif ctx.risk == Risk.HIGH:  # C-03: a notification on every run of a high-risk device
            await self.notify(
                "high_risk_run",
                entry.explanation,
                level="warning" if result == RunResult.CANCELLED else "info",
                run_id=ctx.run_id,
                device_id=ctx.device_id,
                situation_id=ctx.situation_id,
            )

    # --- after a failure, the resident chooses (C-09) -------------------------------------------

    def settled_run(self, run_id: str) -> tuple[Run, dict[str, Any] | None]:
        """The ended run and its plan item, if it came from a situation still kept in memory or the table."""
        with self.db() as db:
            run = db.get(Run, run_id)
            if run is None:
                raise KeyError(run_id)
            sit = next((s for s in self.situations.values() if any(i["run_id"] == run_id for i in s["plan"])), None)
            if sit is None:
                rows = db.scalars(select(Situation).order_by(Situation.received_at.desc()).limit(50)).all()
                for row in rows:
                    if any(i["run_id"] == run_id for i in row.plan):
                        sit = situation_view(row)
                        break
            return run, sit

    def start_retry(self, run: Run, sit: dict[str, Any] | None) -> RunCtx | None:
        """Opens the new run and starts the robot on it; None without a Verified mapping (C-08). Raises Busy
        before anything is written."""
        if self.busy:
            raise Busy(f"the robot is busy with {self.task_name}")
        item = self.plan_item(run.device_id, action_of(run, sit))
        if item is None:
            return None
        item["decision"] = Decision.ACT
        ctx = self.open_run(Trigger(run.trigger), item, sit["situation_id"] if sit else None, retry_of=run.run_id)
        self.start("retry", lambda: self.retry(ctx, run, sit))
        return ctx

    async def retry(self, ctx: RunCtx, run: Run, sit: dict[str, Any] | None) -> None:
        if sit is not None:
            for i in sit["plan"]:
                if i["run_id"] == run.run_id:
                    i.update(run_id=ctx.run_id, result=None, reason=None, resolution="retry")
            sit["status"] = "running"
            await self.save_situation(sit)
        await self.progress(ctx, Step.DECIDE, "you asked to try again", Stage.S4)
        v = self.add_visit(ctx, ctx.press_spot or HOME_SPOT, "requested")
        self.robot.plan = [v["spot_id"]]
        await self.visits_event()
        await self.progress(ctx, Step.QUEUED, "waits for the robot", Stage.S6, Channel.ARM)
        await self.press_visit(v, ctx)
        await self.go_home()
        if sit is not None:
            sit["status"] = self.situation_end(sit)
            await self.save_situation(sit)

    async def leave(self, run: Run, sit: dict[str, Any] | None) -> None:
        if sit is not None:
            for i in sit["plan"]:
                if i["run_id"] == run.run_id:
                    i["resolution"] = "leave"
            sit["status"] = self.situation_end(sit)
            await self.save_situation(sit)
        await self.notify("left_as_is", f"Left as it is: {run.entry['intent']}.", run_id=run.run_id)

    # --- a move order from the resident (FR-09, FR-10) --------------------------------------------

    def start_route(self, stops: list[str]) -> dict[str, Any]:
        """`stops` are spot ids, already resolved. Raises Busy."""
        if self.busy:
            raise Busy(f"the robot is busy with {self.task_name}")
        order = {"route_id": new_id("rt"), "stops": list(stops), "reached": 0, "state": "moving", "reason": None}
        self.start("route", lambda: self.drive(order))
        self.route = order
        return order

    async def route_event(self) -> None:
        if self.route is not None:
            await self.emit("robot.route", dict(self.route))

    async def drive(self, order: dict[str, Any]) -> None:
        await self.route_event()
        self.robot.plan = list(order["stops"])
        for spot in order["stops"]:
            await self.set_task(Step.GATE, f"safety gate: nobody on the way to {spot}")
            ok, why = await self.answers.route_gate(spot)
            if not ok:  # C-04: no movement
                order.update(state="cancelled", reason=f"safety gate failed: {why}")
                self.robot.task, self.robot.plan = None, []
                self.robot.state, self.robot.ral_state = "idle", "idle"
                await self.route_event()
                await self.robot_event()
                await self.notify("route_cancelled", f"I did not move: {why}.", "warning", route_id=order["route_id"])
                return
            await self.set_task(Step.MOVE, f"go to {spot}")
            await self.move_to(spot)
            if self.robot.plan and self.robot.plan[0] == spot:
                self.robot.plan.pop(0)
            order["reached"] += 1
            await self.route_event()
        order["state"] = "done"
        self.robot.task, self.robot.plan = None, []
        self.robot.state, self.robot.ral_state = "idle", "idle"
        await self.route_event()
        await self.robot_event()

    # --- emergency stop (FR-10) -----------------------------------------------------------------

    async def stop(self) -> str:
        """The hand first; then the task, the questions, the open runs, the visits and the situations."""
        try:
            await self.ral.stop()
            ral = "stopped"
        except Exception:
            log.exception("the RAL did not stop")
            ral = "error"
        self.robot.state, self.robot.ral_state, self.robot.task, self.robot.plan = "stopped", ral, None, []
        if self.busy:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        for fut in self.waiting.values():
            if not fut.done():
                fut.cancel()
        self.waiting.clear()
        for ctx in list(self.open.values()):
            await self.close(ctx, RunResult.CANCELLED, EMERGENCY)
        for v in self.visits:
            if v["status"] in ("waiting", "moving", "pressing", "checking"):
                v["status"] = "cancelled"
        for sit in list(self.situations.values()):
            if sit["status"] in ACTIVE:
                sit["status"] = "cancelled"
                await self.save_situation(sit)
        if self.route is not None and self.route["state"] == "moving":
            self.route.update(state="cancelled", reason=EMERGENCY)
            await self.route_event()
        await self.visits_event()
        await self.robot_event()
        await self.notify("stop", "Emergency stop: the hand stopped and the current run was cancelled.", "warning")
        return ral


def action_of(run: Run, sit: dict[str, Any] | None) -> DeviceAction:
    """The run's action: from its plan item, or from its intent ("turn off the ...") when it had no situation."""
    for i in (sit or {}).get("plan", []):
        if i["run_id"] == run.run_id:
            return DeviceAction(i["action"])
    return DeviceAction(run.entry["intent"].split()[1])
