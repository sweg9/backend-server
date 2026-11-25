"""The real answers for the orchestrator, over the link to the enclave (wiki: Hub messages).

- `decide`: the enclave's autonomy policy (FR-06, C-03). A paused home only suggests (FR-06), without asking.
- `gate` and `route_gate`: a photo now and `people` on it (FR-10, C-04). The press gate's photo is also the
  `before` photo of the camera check that follows the press.
- `look_before`: the `before` photo for a wireless command the camera has to check (IR).
- `verify`: the platform's report when there is one (WIFI, BT), else the camera: `check` with `purpose:
  verify` on the before photo and one taken now (FR-08).

Every answer that does not come in time, and every photo the Reader cannot give, goes on the safe side
(wiki: Hub messages > Answers that do not come): suggest, no press, unsure.

Not built yet: `refind` before the press (FR-09). The hub does not store a control's box yet (only
`panel_point`), and the scan that would save it is not built; until then the gate does not check the
control's position, and the press uses the first actuator.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.common.messages import (
    Check,
    CheckResult,
    Decide,
    DecideDevice,
    DecideMapping,
    DecideResult,
    DecideRoutine,
    Envelope,
    Msg,
    People,
    PeopleResult,
    Sealed,
)
from backend.common.settings import HubSettings
from backend.common.vocab import Channel, CheckAnswer, Decision, DeviceAction, MappingStatus, Trigger
from backend.hub.channels.base import SendResult
from backend.hub.db.models import Device, Feedback, Mapping

log = logging.getLogger(__name__)
Ask = Callable[[str, Msg, float], Awaitable[Envelope | None]]  # HubLink.ask without run_id
Photo = Callable[[], Awaitable[Sealed | None]]  # Reader.photo
WHOLE_VIEW = (0.0, 0.0, 1.0, 1.0)


def reach(s: HubSettings) -> tuple[float, float, float, float]:
    """The work area of the hand: every part of the image some actuator faces."""
    boxes = [a.faces for a in s.actuators]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


class LinkAnswers:
    def __init__(self, ask: Ask, photo: Photo, db: sessionmaker, s: HubSettings):
        self.ask = ask
        self.photo = photo
        self.db = db
        self.s = s
        self.work_area = reach(s)
        self._before: dict[str, Sealed] = {}  # device_id -> the photo just before the last command

    # --- S4: decide ----------------------------------------------------------------------------

    async def decide(self, item: dict[str, Any], paused: bool, trigger: Trigger) -> Decision:
        if paused:
            return Decision.SUGGEST  # FR-06: the resident paused the home
        req = self._decide_request(item, trigger)
        if req is None:
            return Decision.SUGGEST
        answer = await self.ask("decide", req, self.s.decide_timeout_s)
        if answer is None:
            return Decision.SUGGEST  # FR-05: no answer means only suggest
        return DecideResult.model_validate(answer.payload).decision

    def _decide_request(self, item: dict[str, Any], trigger: Trigger) -> Decide | None:
        with self.db() as db:
            device = db.get(Device, item["device_id"])
            if device is None:
                return None
            routine = None
            if item.get("routine_id") and item.get("independence"):
                routine = DecideRoutine(
                    independence=item["independence"], accept_share=self._accept_share(db, item["routine_id"])
                )
        return Decide(
            trigger=trigger,
            device=DecideDevice(type=device.type, risk=device.risk),
            action=item["action"],
            mapping=DecideMapping(status=MappingStatus.VERIFIED, confidence=item["confidence"]),  # C-08: plan_item
            routine=routine,
            confidence=item["confidence"],
        )

    @staticmethod
    def _accept_share(db, routine_id: str) -> float:
        """Accepted answers among the resident's accept and reject answers; 1 before any answer."""
        rows = dict(
            db.execute(
                select(Feedback.kind, func.count())
                .where(Feedback.routine_id == routine_id, Feedback.kind.in_(("accept", "reject")))
                .group_by(Feedback.kind)
            ).all()
        )
        answered = rows.get("accept", 0) + rows.get("reject", 0)
        return rows.get("accept", 0) / answered if answered else 1.0

    # --- S6: the safety gate (C-04, FR-10) -----------------------------------------------------

    async def gate(self, device_id: str) -> tuple[bool, str | None]:
        self._before.pop(device_id, None)
        photo = await self.photo()
        if photo is None:
            return False, "no photo for the safety gate"
        ok, why = await self._people(photo, self.work_area)
        if ok:
            self._before[device_id] = photo  # the same view, just before the press
        return ok, why

    async def route_gate(self, spot_id: str) -> tuple[bool, str | None]:
        photo = await self.photo()
        if photo is None:
            return False, "no photo for the safety gate"
        return await self._people(photo, WHOLE_VIEW)

    async def _people(self, photo: Sealed, area) -> tuple[bool, str | None]:
        answer = await self.ask("people", People(photo=photo, work_area=area), self.s.people_timeout_s)
        if answer is None:
            return False, "no answer from the safety gate"
        r = PeopleResult.model_validate(answer.payload)
        return (True, None) if r.clear else (False, r.reason)

    # --- S5, S6: the effect check (FR-08) ------------------------------------------------------

    async def look_before(self, device_id: str, channel: Channel) -> None:
        self._before.pop(device_id, None)
        if channel == Channel.IR:  # WIFI and BT report the state themselves
            photo = await self.photo()
            if photo is not None:
                self._before[device_id] = photo

    async def verify(
        self, device_id: str, channel: Channel, action: DeviceAction, sent: SendResult | None
    ) -> tuple[CheckAnswer, str | None]:
        if sent is not None and sent.reported_state is not None:
            if sent.reported_state == action:
                return CheckAnswer.YES, None
            return CheckAnswer.NO, f"the platform says the device is {sent.reported_state}"
        before = self._before.get(device_id)  # kept for the second look of an unsure check
        if before is None:
            return CheckAnswer.UNSURE, "no photo from before the command"
        target = self._device_view(device_id)
        if target is None:
            return CheckAnswer.UNSURE, "the device has no Verified mapping to look at"
        after = await self.photo()
        if after is None:
            return CheckAnswer.UNSURE, "no photo after the command"
        box, device_type = target
        req = Check(
            purpose="verify", before=[before], after=[after], device_box=box, device_type=device_type, action=action
        )
        answer = await self.ask("check", req, self.s.check_timeout_s)
        if answer is None:
            return CheckAnswer.UNSURE, "no check answer"
        r = CheckResult.model_validate(answer.payload)
        if r.answer is None:
            return CheckAnswer.UNSURE, r.reason
        return r.answer, None if r.answer == CheckAnswer.YES else r.reason

    def _device_view(self, device_id: str):
        with self.db() as db:
            row = db.execute(
                select(Mapping.device_box, Device.type)
                .join(Device, Device.device_id == Mapping.device_id)
                .where(Mapping.device_id == device_id, Mapping.status == MappingStatus.VERIFIED)
            ).first()
        return (tuple(row[0]), row[1]) if row else None
