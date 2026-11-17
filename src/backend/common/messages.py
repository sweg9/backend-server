"""Hub messages: the envelope and every payload (wiki: API > Hub messages).

The hub and the server talk only through these models (wiki: Modules, import rules).
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.common.vocab import (
    Channel,
    CheckAnswer,
    ControlKind,
    ControlState,
    DeviceAction,
    DeviceType,
    HandOption,
    Risk,
    RunResult,
    Stage,
)

Ratio = Annotated[float, Field(ge=0, le=1)]
Box = tuple[Ratio, Ratio, Ratio, Ratio]
Point = tuple[Ratio, Ratio]
Fingerprint = Annotated[list[float], Field(min_length=384, max_length=384)]
OnOff = Literal["on", "off"]


class Msg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Crop(Msg):
    crop_id: str
    jpeg_b64: str
    w: Annotated[int, Field(gt=0, le=256)]
    h: Annotated[int, Field(gt=0, le=256)]


# --- Setup: S1 and S2 -------------------------------------------------------


class ScanStart(Msg):
    scan_id: str
    spot_id: str
    scan_s: Annotated[int, Field(gt=0)]
    watch_s: Annotated[int, Field(ge=0)]


class ControlFound(Msg):
    scan_id: str
    local_id: str
    kind: ControlKind
    score: Ratio
    state: ControlState
    state_confidence: Ratio
    box: Box
    panel_point: Point | None
    fingerprint: Fingerprint
    crop: Crop
    spot_id: str


class ScanDone(Msg):
    scan_id: str
    found: Annotated[int, Field(ge=0)]
    skipped: Annotated[int, Field(ge=0)]
    reason: str | None


class StateChange(Msg):
    before: ControlState
    after: ControlState


class MappingCandidate(Msg):
    scan_id: str
    local_id: str
    method: Literal["watch", "test"]
    control_state: StateChange
    device_box: Box
    before: Crop
    after: Crop
    change_score: Ratio
    device_type: DeviceType | None
    device_score: Ratio | None
    spot_id: str
    room: str


# --- Uses for habits: S3 -----------------------------------------------------


class WatchedControl(Msg):
    control_id: str
    kind: ControlKind
    fingerprint: Fingerprint
    seen_spot_id: str
    box: Box


class WatchSet(Msg):
    enabled: bool
    controls: list[WatchedControl]


class ControlUsed(Msg):
    control_id: str
    before: OnOff
    after: OnOff
    at: datetime
    confidence: Ratio


# --- Runtime: S4 to S7 -------------------------------------------------------


class RunDevice(Msg):
    device_id: str
    name: str
    risk: Risk


class RunControl(Msg):
    control_id: str
    kind: ControlKind
    fingerprint: Fingerprint
    panel_point: Point | None
    press_spot_id: str | None


class RunCheck(Msg):
    device_box: Box
    spot_id: str
    device_type: DeviceType


class ChannelScore(Msg):
    channel: Channel
    success_score: Ratio
    latency_ms: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _no_arm(self):
        if self.channel == Channel.ARM:
            raise ValueError("ARM is never listed in run.start; it is always the last resort")
        return self


class RunStart(Msg):
    mapping_id: str
    device: RunDevice
    action: DeviceAction
    decision: Literal["act", "ask"]
    control: RunControl
    check: RunCheck
    channels: list[ChannelScore]


class RunProgress(Msg):
    stage: Literal["S5", "S6"]
    step: Literal["channel", "gate", "move", "press", "check"]
    status: Literal["started", "ok", "fail", "unsure", "skipped"]
    channel: Channel | None
    detail: str | None
    latency_ms: Annotated[int, Field(ge=0)] | None


class Checks(Msg):
    source: Literal["platform", "camera"] | None = None  # set in the run entry (L-09), not in run.result
    main: CheckAnswer | None
    second: Any = None  # a check answer or a small object such as {"room_brighter": "yes"}
    disagree: bool


class StageItem(Msg):
    stage: Stage
    status: Literal["ok", "fail", "skipped"]
    latency_ms: Annotated[int, Field(ge=0)]
    reason: str | None = None


class Attempt(Msg):
    channel: Channel
    latency_ms: Annotated[int, Field(ge=0)]
    answer: CheckAnswer
    source: Literal["platform", "camera"] | None = None
    gate: str | None = None


class RunResultMsg(Msg):
    result: RunResult
    reason: str | None
    channel: Channel | None
    checks: Checks
    stages: list[StageItem]
    attempts: list[Attempt]
    press_spot_id: str | None
    last_crop_id: str | None

    @model_validator(mode="after")
    def _reason_unless_success(self):
        if self.result != RunResult.SUCCESS and not self.reason:
            raise ValueError("reason is required unless the result is success")
        return self


class Stop(Msg):
    source: Literal["app", "key"]


class RalStatus(Msg):
    impl: HandOption
    state: Literal["idle", "busy", "stopped", "error"]


class HubStatus(Msg):
    camera: Literal["ok", "down"]
    ral: RalStatus
    run_id: str | None
    queued: Annotated[int, Field(ge=0)]
    watching: Annotated[int, Field(ge=0)]


class Ack(Msg):
    ids: Annotated[list[str], Field(min_length=1)]


# --- Envelope -----------------------------------------------------------------

PAYLOADS: dict[str, type[Msg]] = {
    "scan.start": ScanStart,
    "control.found": ControlFound,
    "scan.done": ScanDone,
    "mapping.candidate": MappingCandidate,
    "watch.set": WatchSet,
    "control.used": ControlUsed,
    "run.start": RunStart,
    "run.progress": RunProgress,
    "run.result": RunResultMsg,
    "stop": Stop,
    "hub.status": HubStatus,
    "ack": Ack,
}

# Messages that change a record are confirmed with ack; only these wait in the hub's outbox.
HUB_OUTBOX_TYPES = {"control.found", "scan.done", "mapping.candidate", "control.used", "run.result"}
RUN_TYPES = {"run.start", "run.progress", "run.result"}


class Envelope(Msg):
    v: Literal[1] = 1
    type: str
    id: str
    run_id: str | None
    sent_at: datetime
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _check(self):
        if self.type not in PAYLOADS:
            raise ValueError(f"unknown message type {self.type!r}")
        if self.type in RUN_TYPES and not self.run_id:
            raise ValueError(f"{self.type} belongs to a run and needs run_id")
        return self

    def body(self) -> Msg:
        """The payload, checked against its model."""
        return PAYLOADS[self.type].model_validate(self.payload)
