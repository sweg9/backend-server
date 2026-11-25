"""recognize, check, refind, people and decide: calls into `ai_engine` and answers in the shapes of
common/messages.py (FR-01, FR-02, FR-06, FR-08 to FR-10, A-01 to A-08).

The enclave program (main.py) opens each sealed photo with unseal.py and hands the opened frames to
the `Engine`. Only results leave the enclave (A-07): no frame, crop or fingerprint is in an answer.
A request that fails (a model error, a bad photo, an unknown room) gets no answer; the hub then
handles it on the safe side (wiki: Hub messages > Answers that do not come).
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import numpy as np
from ai_engine import Actuator, EngineError, SavedControl
from ai_engine import Fingerprint as EngineFingerprint
from ai_engine.images import crop
from pydantic import ValidationError

from backend.common.ids import new_id
from backend.common.messages import (
    ANSWERS,
    Check,
    CheckResult,
    Decide,
    DecideResult,
    Envelope,
    FoundControl,
    Gang,
    LinkGuess,
    Msg,
    People,
    PeopleResult,
    PressPlan,
    Recognize,
    RecognizeResult,
    Refind,
    RefindResult,
    Sealed,
)
from backend.common.sealing import SealError
from backend.common.settings import RiskSettings
from backend.common.vocab import DeviceAction, DeviceType
from backend.enclave.policy import decide

log = logging.getLogger(__name__)

PhotoOpener = Callable[[Sealed], np.ndarray]  # a sealed photo -> uint8 BGR array (unseal.py)
DEVICE_TYPES = {t.value for t in DeviceType}


def _center(box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _overlaps(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


class Handlers:
    """One handler per enclave request. `engine` is an `ai_engine.Engine` (or `FakeEngine` in tests),
    `engine_settings` the ai-engine settings it was loaded with, `risk` the policy settings."""

    def __init__(self, engine, engine_settings: dict[str, Any], risk: RiskSettings, open_photo: PhotoOpener):
        self.engine = engine
        self.risk = risk
        self.open = open_photo
        self.fingerprint_model = engine_settings["paths"]["fingerprint_weights"]  # None until DINOv2 exists
        self.max_shift = engine_settings["refind"]["max_shift"]
        self.people_conf = engine_settings["people"]["conf"]
        self.by_type: dict[str, Callable[[Any], Msg]] = {
            "recognize": self.recognize,
            "check": self.check,
            "refind": self.refind,
            "people": self.people,
            "decide": self.decide,
        }

    # ---------- the envelope

    def answer(self, request: Envelope) -> Envelope | None:
        """The answer envelope, or None when the request failed (logged by type and id only, L-07)."""
        handler = self.by_type.get(request.type)
        if handler is None:
            log.warning("not an enclave request: %s", request.type, extra={"msg_id": request.id})
            return None
        try:
            result = handler(request.body())
        except (EngineError, SealError, ValueError, ValidationError) as e:
            # ValidationError is a ValueError; listed for the reader. SealError: a photo that does not
            # open (C-05). Only the error's class is logged, since its text may quote the payload.
            log.warning("%s failed: %s", request.type, type(e).__name__, extra={"msg_id": request.id})
            return None
        return Envelope(
            type=ANSWERS[request.type],
            id=new_id("m"),
            re=request.id,
            run_id=request.run_id,
            sent_at=datetime.now(UTC),
            payload=result.model_dump(mode="json"),
        )

    # ---------- S1, S2, S3: recognize (FR-01, FR-02)

    def recognize(self, req: Recognize) -> RecognizeResult:
        frame = self.open(req.photo)
        person = any(p.kind == "person" for p in self.engine.find_people(frame))  # C-05: the hub keeps nothing
        controls = self.engine.detect_controls(frame)
        guesses = self.engine.link_guess(controls, req.room)
        ids = self._match_known(controls, req.known or [])
        found = []
        for c, g, control_id in zip(controls, guesses, ids, strict=True):
            part = crop(frame, c.box)
            clear = self.engine.is_clear(part, c.confidence)
            state = self.engine.read_state(part, c.kind)
            found.append(
                FoundControl(
                    kind=c.kind,
                    box=c.box,
                    score=c.confidence,
                    clear=clear.clear,
                    state=state.state,
                    state_confidence=state.confidence,
                    gang=Gang(count=g.gang.count, index=g.gang.index + 1) if g.gang else None,  # 1-based on the wire
                    link_guess=LinkGuess(device_class=g.device_class, p=g.p) if g.device_class else None,
                    control_id=control_id,
                )
            )
        return RecognizeResult(controls=found, person=person)

    def _match_known(self, controls, known) -> list[str | None]:
        """For `watch`: which known control each detection is. Same kind, center moved at most
        refind.max_shift, nearest first; each known control is matched once."""
        pairs = []
        for i, c in enumerate(controls):
            for k in known:
                shift = max(abs(a - b) for a, b in zip(_center(c.box), _center(k.box), strict=True))
                if c.kind == k.kind and shift <= self.max_shift:
                    pairs.append((shift, i, k.control_id))
        out: list[str | None] = [None] * len(controls)
        used = set()
        for _, i, control_id in sorted(pairs):
            if out[i] is None and control_id not in used:
                out[i] = control_id
                used.add(control_id)
        return out

    # ---------- S2, S5, S6: check (FR-02, FR-08)

    def check(self, req: Check) -> CheckResult:
        before = [self.open(s) for s in req.before]
        after = [self.open(s) for s in req.after]
        if req.purpose == "verify":
            return self._verify(req, before, after)
        return self._map(before, after)

    def _map(self, before, after) -> CheckResult:
        """Which device changed after the resident used a control. A device only where the region
        changed clearly and the device detector finds one there (C-08)."""
        m = self.engine.measure_change(before[0], after[-1])
        change = {"brighter": "off_to_on", "darker": "on_to_off"}.get(m.direction, "none")
        if not m.clear:
            return CheckResult(
                change="none", answer=None, device_box=None, device_type=None, confidence=m.confidence, reason=m.reason
            )
        best, best_score = None, -1.0
        for d in self.engine.detect_devices(after[-1]):
            if d.type not in DEVICE_TYPES:
                continue
            score = self.engine.measure_change(crop(before[0], d.box), crop(after[-1], d.box)).change_score
            if score > best_score:
                best, best_score = d, score
        if best is None:
            return CheckResult(
                change=change,
                answer=None,
                device_box=None,
                device_type=None,
                confidence=m.confidence,
                reason=f"{m.reason}; no device found where it changed",
            )
        return CheckResult(
            change=change,
            answer=None,
            device_box=best.box,
            device_type=best.type,
            confidence=min(m.confidence, best.confidence),
            reason=f"{m.reason}; {best.reason}",
        )

    def _verify(self, req: Check, before, after) -> CheckResult:
        """Did the command work (FR-08). The hub decides on a second look; nothing here presses (C-09)."""
        r = self.engine.check_effect(before, after, req.device_box, req.device_type.value, req.action.value)
        change = "none"
        if r.answer == "yes":
            change = "off_to_on" if req.action == DeviceAction.ON else "on_to_off"
        return CheckResult(
            change=change,
            answer=r.answer,
            device_box=req.device_box,
            device_type=req.device_type,
            confidence=r.confidence,
            reason=r.reason,
        )

    # ---------- S6: refind and people (FR-09, FR-10)

    def refind(self, req: Refind) -> RefindResult:
        frame = self.open(req.photo)
        fp = None
        if req.control.fingerprint is not None and self.fingerprint_model is not None:
            fp = EngineFingerprint(vector=tuple(req.control.fingerprint), model=str(self.fingerprint_model))
        saved = SavedControl(kind=req.control.kind.value, box=req.control.box, fingerprint=fp)
        r = self.engine.refind_control(frame, saved)
        if not r.found:
            return RefindResult(found=False, box=None, plan=None, confidence=r.confidence, reason=r.reason)
        actuators = [Actuator(id=a.id, region=a.faces) for a in req.actuators]
        plan = self.engine.plan_press(r, req.control.kind.value, actuators)
        if plan.actuator is None:  # the hub cancels the run without a plan (FR-09)
            return RefindResult(found=True, box=r.box, plan=None, confidence=plan.confidence, reason=plan.reason)
        return RefindResult(
            found=True,
            box=r.box,
            plan=PressPlan(actuator=plan.actuator, push_ms=plan.push_ms),
            confidence=min(r.confidence, plan.confidence),
            reason=f"{r.reason}; {plan.reason}",
        )

    def people(self, req: People) -> PeopleResult:
        """Clear only when no person or pet overlaps the work area at all (FR-10, C-04)."""
        frame = self.open(req.photo)
        inside = [p for p in self.engine.find_people(frame) if _overlaps(p.box, req.work_area)]
        if inside:
            worst = max(inside, key=lambda p: p.confidence)
            return PeopleResult(clear=False, confidence=worst.confidence, reason=f"{worst.kind} in the work area")
        return PeopleResult(clear=True, confidence=1 - self.people_conf, reason="no person or pet in the work area")

    # ---------- S4: decide (FR-06)

    def decide(self, req: Decide) -> DecideResult:
        return decide(req, self.risk)
