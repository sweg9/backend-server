"""Demo scenarios: a trigger source with fixed answers, for the web app's development and the demo.

Started only by `POST /scenarios/{name}/start`, which answers 404 unless the hub runs with
`--demo-scenarios`. A scenario stands in for what is not built or not at hand: the sensors that
trigger, the enclave's decisions, the wireless platforms and the camera's checks. Everything after
that is the real hub: the orchestrator, the run entries, the records and the events.

- `new_control` (demo scenario 1, FR-01 to FR-04): a scan finds a new switch and guesses "ceiling light
  83%"; the resident flips it; the check shows the stand lamp changed, so the mapping is fixed and
  Verified; a habit routine is added.
- `leave_home` (demo scenario 3, FR-05 to FR-12): "you left home" comes in; the plan turns off what is
  on; TV and heater go off over Wi-Fi, the purifier gets no Bluetooth answer and the bedroom light no
  IR effect, two lights have no channel; the robot visits and presses; the bedroom light shows no change
  after the first press, so the robot passes the gate again and presses once more, which works; the
  kitchen light stays unsure after one more look, so the resident chooses retry or leave.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from backend.common.ids import new_id
from backend.common.vocab import (
    Channel,
    CheckAnswer,
    ControlKind,
    ControlState,
    Decision,
    DeviceAction,
    Independence,
    MappingStatus,
    RoutineSource,
    Stage,
    Trigger,
)
from backend.hub.channels.base import SendResult
from backend.hub.db.models import Control, Device, Mapping, Routine, Scan
from backend.hub.orchestrator import Orchestrator
from backend.hub.prefs import proactive
from backend.hub.registry import add_control, add_crop, add_mapping, now, verify_mapping
from backend.hub.views import control_view, mapping_view, routine_view

NO_PHOTO = "(scenario: no photo)"


class NotReady(Exception):
    """The records lack what the scenario needs, for example after the resident renamed a device."""


# --- fixed answers -----------------------------------------------------------------------------


@dataclass
class Outcome:
    answer: CheckAnswer
    reason: str | None
    times: int = 1  # how many checks it answers; then the device behaves (a retry works)


@dataclass
class ScriptedAnswers:
    """Decisions, checks and gates from the scenario instead of the enclave and the camera."""

    outcomes: dict[tuple[str, Channel], Outcome] = field(default_factory=dict)
    low_confidence: float = 0.5

    async def look_before(self, device_id: str, channel: Channel) -> None:
        pass

    async def decide(self, item: dict[str, Any], paused: bool, trigger: Trigger) -> Decision:
        # The hub's own rules (FR-06 pause, the routine's independence) and a confidence floor, in place of
        # the enclave's policy.
        if paused or item.get("independence") == Independence.SUGGEST_ONLY or item["confidence"] < self.low_confidence:
            return Decision.SUGGEST
        if item.get("independence") == Independence.ASK_FIRST:
            return Decision.ASK
        return Decision.ACT

    async def verify(self, device_id, channel, action, sent: SendResult | None) -> tuple[CheckAnswer, str | None]:
        o = self.outcomes.get((device_id, channel))
        if o is not None and o.times > 0:
            o.times -= 1
            return o.answer, o.reason
        if sent is not None and sent.reported_state is not None:
            return (
                (CheckAnswer.YES, None) if sent.reported_state == action else (CheckAnswer.NO, "the platform says no")
            )
        return CheckAnswer.YES, None

    async def gate(self, device_id: str) -> tuple[bool, str | None]:
        return True, None

    async def route_gate(self, spot_id: str) -> tuple[bool, str | None]:
        return True, None


class ScriptedAdapter:
    """A wireless channel that answers as the scenario says: not reached for the listed devices."""

    def __init__(self, channel: Channel, unreached: dict[str, str] | None = None):
        self.channel = channel
        self.unreached = unreached or {}

    async def reachable(self, device_id: str, address: dict | None) -> bool:
        return device_id not in self.unreached

    async def send(self, device_id: str, address: dict | None, action: DeviceAction) -> SendResult:
        if device_id in self.unreached:
            return SendResult(sent=False, reported_state=None, latency_ms=5000, reason=self.unreached[device_id])
        reported = action if self.channel in (Channel.WIFI, Channel.BT) else None
        return SendResult(sent=True, reported_state=reported, latency_ms=1200)


def device_by_name(db, name: str) -> Device:
    d = db.scalar(select(Device).where(Device.name == name))
    if d is None:
        raise NotReady(f"no device named {name!r}; run `backend seed-demo` on an empty database")
    return d


# --- demo scenario 3 ---------------------------------------------------------------------------


async def leave_home(orch: Orchestrator) -> None:
    with orch.db() as db:
        names = {n: device_by_name(db, n).device_id for n in ("Bedroom ceiling light", "Air purifier", "Kitchen light")}
        # Before it starts, the devices with a Verified mapping are back on, as if the resident came home.
        wants = []
        for m in db.scalars(select(Mapping).where(Mapping.status == MappingStatus.VERIFIED)):
            c = db.get(Control, m.control_id)
            if c.state != ControlState.ON:
                c.state = ControlState.ON
                changed = control_view(c)
                db.flush()
                await orch.emit("control", changed)
            wants.append((m.device_id, DeviceAction.OFF))
        paused = proactive(db)["paused"]
        db.commit()
    orch.adapters = {
        Channel.IR: ScriptedAdapter(Channel.IR),
        Channel.WIFI: ScriptedAdapter(Channel.WIFI),
        Channel.BT: ScriptedAdapter(Channel.BT, {names["Air purifier"]: "no answer over Bluetooth within 5 s"}),
    }
    orch.answers = ScriptedAnswers(
        {
            (names["Bedroom ceiling light"], Channel.IR): Outcome(
                CheckAnswer.NO, "the camera saw no change after the IR command"
            ),
            (names["Bedroom ceiling light"], Channel.ARM): Outcome(  # FR-11: the second press works
                CheckAnswer.NO, "the camera saw no change after the press"
            ),
            (names["Kitchen light"], Channel.ARM): Outcome(
                CheckAnswer.UNSURE, "the camera could not tell whether the light went off", times=2
            ),
        }
    )
    await orch.handle_situation(
        "leave_home",
        Trigger.SENSOR,
        "You left home: the front door closed and nobody has moved inside for 2 minutes.",
        wants,
        paused,
    )


# --- demo scenario 1 ---------------------------------------------------------------------------


def detection(kind, box, state, control_id, guess, caution=False, reason=None, score=0.9) -> dict[str, Any]:
    d = {
        "kind": kind,
        "box": box,
        "score": score,
        "clear": True,
        "state": state,
        "state_confidence": 0.9,
        "control_id": control_id,
        "link_guess": guess,
        "caution": caution,
    }
    if reason:
        d["caution_reason"] = reason
    return d


async def new_control(orch: Orchestrator) -> None:
    with orch.db() as db:
        ceiling = device_by_name(db, "Living room ceiling light")
        stand = device_by_name(db, "Living room stand lamp")
        known = db.scalar(select(Control).where(Control.press_spot_id == "living_wall_1"))
        strip = db.scalar(
            select(Control).where(Control.press_spot_id == "living_tv", Control.kind == ControlKind.POWER_STRIP)
        )
        if known is None or strip is None:
            raise NotReady("the demo home's living room controls are missing")
        known_id, known_state, strip_id = known.control_id, known.state, strip.control_id
        scan = Scan(
            scan_id=new_id("sc"),
            spot_id="living_wall_1",
            started_at=now(),
            done_at=None,
            found=None,
            skipped=None,
            reason=None,
        )
        db.add(scan)
        db.commit()
        scan_id = scan.scan_id

    async def scan_event(**data) -> None:
        await orch.emit("scan", {"scan_id": scan_id, **data})

    await orch.set_task("scan the room", Stage.S1)
    await scan_event(status="started", room=ceiling.room)
    await orch.move_to("living_wall_1")
    orch.robot.state = "scanning"
    await orch.robot_event()

    new_id_ = None
    mapping_id = None
    guess = {"device_id": ceiling.device_id, "device_class": "ceiling_light", "label": "Ceiling light", "p": 0.83}
    for frame in range(1, 7):
        j = 0.004 * (frame % 3 - 1)
        dets = [
            detection(
                "switch",
                [0.42 + j, 0.31, 0.51 + j, 0.44],
                known_state,
                known_id,
                {"device_id": ceiling.device_id, "label": ceiling.name, "p": 0.96, "status": "Verified"},
            ),
            detection("switch", [0.52 + j, 0.31, 0.61 + j, 0.44], "off", new_id_, guess, score=0.89),
        ]
        await scan_event(status="running", spot_id="living_wall_1", frame=frame, detections=dets, person=False)
        if frame == 3:  # clear in three photos: a new control and a Pending mapping to the guess (FR-01, FR-02)
            with orch.db() as db:
                c = add_control(
                    db, ControlKind.SWITCH, ControlState.OFF, 0.9, ["press"], [0.565, 0.375], "living_wall_1"
                )
                m = add_mapping(
                    db,
                    c,
                    ceiling.device_id,
                    0.83,
                    before=add_crop(db, "device_before", NO_PHOTO),
                    after=add_crop(db, "device_after", NO_PHOTO),
                    device_box=[0.2, 0.05, 0.6, 0.3],
                    change_score=0.0,
                    device_type_seen="light",
                    reason="guessed from the room and the panel",
                )
                db.commit()
                new_id_, mapping_id = c.control_id, m.mapping_id
                control_event, mapping_event = control_view(c), mapping_view(m)
            await orch.emit("control", control_event)
            await orch.emit("mapping", mapping_event)
        await orch.wait(0.8)

    await orch.move_to("living_tv")
    orch.robot.state = "scanning"
    await orch.robot_event()
    for frame in range(7, 10):
        dets = [
            detection(
                "power_strip",
                [0.48, 0.72, 0.66, 0.86],
                "on",
                strip_id,
                {"device_class": "tv", "label": "TV", "p": 0.9, "status": "Verified"},
                caution=True,
                reason="Power strip: not pressed by the robot unless the risk policy allows it",
                score=0.88,
            )
        ]
        await scan_event(status="running", spot_id="living_tv", frame=frame, detections=dets, person=False)
        await orch.wait(0.8)
    with orch.db() as db:
        row = db.get(Scan, scan_id)
        row.done_at, row.found, row.skipped = now(), 2, 0
        db.commit()
    await scan_event(status="done", found=2, new=1)

    # The resident flips the new switch; the before/after check finds the stand lamp, not the ceiling light.
    await orch.move_to("living_wall_1")
    orch.robot.state = "watching"
    await orch.set_task("watch while you flip the switch", Stage.S2)
    await orch.notify(
        "flip_request", "Please flip the new switch off and on, so I can see what it runs.", control_id=new_id_
    )
    await orch.wait(3.0)
    check = {
        "change": "off_to_on",
        "device_type": "light",
        "device_box": [0.71, 0.4, 0.83, 0.77],
        "device_id": stand.device_id,
        "confidence": 0.9,
        "reason": "the stand lamp region got brighter; the ceiling light did not change",
    }
    await scan_event(
        status="checked",
        control_id=new_id_,
        check=check,
        guess={"device_id": ceiling.device_id, "p": 0.83},
        guess_right=False,
    )
    with orch.db() as db:
        m = db.get(Mapping, mapping_id)
        before = verify_mapping(db, m, stand.device_id, 0.9, "the check saw the stand lamp change")
        c = db.get(Control, new_id_)
        c.state = ControlState.ON
        db.flush()
        mapping_event, control_event = {**mapping_view(m), "previous_device_id": before}, control_view(c)
        top = max((r.priority for r in db.scalars(select(Routine).where(Routine.removed_at.is_(None)))), default=0)
        r = Routine(
            routine_id=new_id("rt"),
            mapping_id=mapping_id,
            action=DeviceAction.OFF,
            time="23:00",
            source=RoutineSource.HABIT,
            independence=Independence.ASK_FIRST,
            priority=top + 1,
            reject_streak=0,
            created_at=now(),
            updated_at=now(),
        )
        db.add(r)
        db.commit()
        routine_event = routine_view(r)
    await orch.emit("mapping", mapping_event)
    await orch.emit("control", control_event)
    await orch.notify(
        "mapping_fixed",
        "My guess was wrong: the new switch runs the stand lamp, not the ceiling light. I fixed the mapping.",
        mapping_id=mapping_id,
    )
    await orch.wait(1.5)
    await orch.emit("routine", routine_event)
    await orch.notify(
        "routine_added",
        "New routine: turn off the stand lamp at 23:00. I will ask first.",
        routine_id=routine_event["routine_id"],
    )
    await orch.go_home()


SCENARIOS: dict[str, tuple[str, Callable[[Orchestrator], Awaitable[None]]]] = {
    "new_control": (
        "Demo scenario 1. A scan finds a new switch, guesses the ceiling light, the resident flips it, the check "
        "shows the stand lamp, the mapping is fixed and Verified, a habit routine is added.",
        new_control,
    ),
    "leave_home": (
        "Demo scenario 3. 'You left home' comes in, the plan turns off what is on, wireless succeeds and fails, "
        "the robot visits and presses, and the kitchen light stays unsure so the resident chooses retry or leave.",
        leave_home,
    ),
}


def start(orch: Orchestrator, name: str, speed: float) -> None:
    """Raises KeyError for an unknown name and Busy while the robot does something else."""
    _, play = SCENARIOS[name]

    async def work() -> None:
        orch.speed = speed
        try:
            await play(orch)
        except NotReady as e:
            await orch.notify("scenario_not_ready", str(e), "warning")
        finally:
            orch.speed = 1.0

    orch.start(name, work)
