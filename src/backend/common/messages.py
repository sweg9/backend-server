"""Hub messages, contract version 2: the envelope and every payload (wiki: API > Hub messages).

The hub keeps the records and runs the runs; the server relays its requests to the Nitro Enclave
and the web app's calls to the hub. The hub, the server and the enclave talk only through these
models (wiki: Modules, import rules).
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.common.vocab import (
    Channel,
    CheckAnswer,
    ControlKind,
    ControlState,
    Decision,
    DeviceAction,
    DeviceType,
    HandOption,
    Independence,
    MappingStatus,
    Risk,
    Stage,
    Trigger,
)

Ratio = Annotated[float, Field(ge=0, le=1)]
Box = tuple[Ratio, Ratio, Ratio, Ratio]
Fingerprint = Annotated[list[float], Field(min_length=384, max_length=384)]
Reason = Annotated[str, Field(min_length=1)]  # A-02: one short sentence from a fixed template
MAX_PHOTO_SIDE = 1280


class Msg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Sealed(Msg):
    """One JPEG photo, encrypted by the hub with a KMS data key (C-05). Only the attested enclave opens it."""

    photo_id: str
    key: str  # the photo's data key, encrypted by KMS, base64
    nonce: str  # AES-256-GCM nonce, base64
    data: str  # the encrypted photo, base64
    w: Annotated[int, Field(gt=0, le=MAX_PHOTO_SIDE)]
    h: Annotated[int, Field(gt=0, le=MAX_PHOTO_SIDE)]


class Answer(Msg):
    """Every answer from the enclave has a confidence and a reason (A-02)."""

    confidence: Ratio
    reason: Reason


# --- Requests to the enclave -------------------------------------------------------------


class KnownControl(Msg):
    control_id: str
    kind: ControlKind
    box: Box


class Recognize(Msg):
    purpose: Literal["scan", "watch"]
    spot_id: str
    room: str
    photo: Sealed
    known: list[KnownControl] | None = None

    @model_validator(mode="after")
    def _known_for_watch(self):
        if self.purpose == "watch" and not self.known:
            raise ValueError("a watch request lists the known controls to look for")
        return self


class Gang(Msg):
    count: Annotated[int, Field(ge=1)]
    index: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _index_in_count(self):
        if self.index > self.count:
            raise ValueError("the key index is larger than the gang count")
        return self


class LinkGuess(Msg):
    device_class: str
    p: Ratio


class FoundControl(Msg):
    kind: ControlKind
    box: Box
    score: Ratio
    clear: bool
    state: ControlState
    state_confidence: Ratio
    gang: Gang | None
    link_guess: LinkGuess | None
    control_id: str | None


class RecognizeResult(Msg):
    controls: list[FoundControl]
    person: bool


class Check(Msg):
    purpose: Literal["map", "verify"]
    before: Annotated[list[Sealed], Field(min_length=1, max_length=5)]
    after: Annotated[list[Sealed], Field(min_length=1, max_length=5)]
    device_box: Box | None
    device_type: DeviceType | None
    action: DeviceAction | None

    @model_validator(mode="after")
    def _verify_needs_the_device(self):
        if self.purpose == "verify" and (self.device_box is None or self.device_type is None or self.action is None):
            raise ValueError("a verify check names the device box, the device type and the action")
        return self


class CheckResult(Answer):
    change: Literal["off_to_on", "on_to_off", "none"]
    answer: CheckAnswer | None
    device_box: Box | None
    device_type: DeviceType | None


class RefindControl(Msg):
    control_id: str
    kind: ControlKind
    box: Box
    fingerprint: Fingerprint | None


class ActuatorFaces(Msg):
    id: str
    faces: Box


class Refind(Msg):
    photo: Sealed
    control: RefindControl
    actuators: Annotated[list[ActuatorFaces], Field(min_length=1)]


class PressPlan(Msg):
    actuator: str
    push_ms: Annotated[int, Field(gt=0)]


class RefindResult(Answer):
    found: bool
    box: Box | None
    plan: PressPlan | None

    @model_validator(mode="after")
    def _plan_only_when_found(self):
        if not self.found and (self.box is not None or self.plan is not None):
            raise ValueError("a control that was not found has no box and no plan")
        return self


class People(Msg):
    photo: Sealed
    work_area: Box


class PeopleResult(Answer):
    clear: bool


class DecideDevice(Msg):
    type: DeviceType
    risk: Risk


class DecideMapping(Msg):
    status: MappingStatus
    confidence: Ratio


class DecideRoutine(Msg):
    independence: Independence
    accept_share: Ratio


class Decide(Msg):
    trigger: Trigger
    device: DecideDevice
    action: DeviceAction
    mapping: DecideMapping
    routine: DecideRoutine | None
    confidence: Ratio


class DecideResult(Answer):
    decision: Decision


# --- Toward the app ------------------------------------------------------------------------


class AppRequest(Msg):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    query: dict[str, str] = {}
    body: Any = None
    session: str | None


class AppResponse(Msg):
    status: Annotated[int, Field(ge=100, le=599)]
    body: Any = None
    set_session: str | None = None  # the cookie to set; "" clears it (login and logout)


class AppEvent(Msg):
    kind: Literal[
        "control",
        "mapping",
        "routine",
        "scan",
        "run.progress",
        "run.result",
        "notification",
        "situation",  # a Situation of the API, on every change of its status or plan
        "visits",  # {visits: Visit[]}, the robot's visit order
        "robot",  # the Robot of the API, on every change of its state
        "robot.route",  # a RobotRoute: when a move order starts, at every stop reached and when it ends
    ]
    data: dict[str, Any]


class Stop(Msg):
    source: Literal["app"]  # the key on the hub and the power switch work without the server


class StopDone(Msg):
    ral: Literal["stopped", "error"]


class RalStatus(Msg):
    impl: HandOption
    state: Literal["idle", "busy", "stopped", "error"]


class HubStatus(Msg):
    camera: Literal["ok", "down"]
    ral: RalStatus
    run_id: str | None
    watching: Annotated[int, Field(ge=0)]


# --- Parts of the run entry (execution-log.md), shared with runlog.py -------------------------


class Checks(Msg):
    source: Literal["platform", "camera"] | None = None
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


# --- Envelope -------------------------------------------------------------------------------

PAYLOADS: dict[str, type[Msg]] = {
    "recognize": Recognize,
    "recognize.result": RecognizeResult,
    "check": Check,
    "check.result": CheckResult,
    "refind": Refind,
    "refind.result": RefindResult,
    "people": People,
    "people.result": PeopleResult,
    "decide": Decide,
    "decide.result": DecideResult,
    "app.request": AppRequest,
    "app.response": AppResponse,
    "app.event": AppEvent,
    "stop": Stop,
    "stop.done": StopDone,
    "hub.status": HubStatus,
}

# Requests the server hands to the enclave over vsock, unopened, and the answer each one gets.
ENCLAVE_REQUESTS = {"recognize", "check", "refind", "people", "decide"}
ANSWERS = {
    "recognize": "recognize.result",
    "check": "check.result",
    "refind": "refind.result",
    "people": "people.result",
    "decide": "decide.result",
    "app.request": "app.response",
    "stop": "stop.done",
}
# Requests that belong to a run carry its run_id.
RUN_TYPES = {"refind", "people", "decide"}


class Envelope(Msg):
    v: Literal[2] = 2
    type: str
    id: str
    re: str | None = None
    run_id: str | None
    sent_at: datetime
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _check(self):
        if self.type not in PAYLOADS:
            raise ValueError(f"unknown message type {self.type!r}")
        is_answer = self.type in ANSWERS.values()
        if is_answer and not self.re:
            raise ValueError(f"{self.type} answers a request and needs re")
        if not is_answer and self.re:
            raise ValueError(f"{self.type} is not an answer and has no re")
        if self.type in RUN_TYPES and not self.run_id:
            raise ValueError(f"{self.type} belongs to a run and needs run_id")
        return self

    def body(self) -> Msg:
        """The payload, checked against its model."""
        return PAYLOADS[self.type].model_validate(self.payload)
