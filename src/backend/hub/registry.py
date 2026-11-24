"""Same control or new one, finds or creates the device, creates and updates mappings from
`check.result` (FR-01, FR-02, C-08).

Built so far: the writes themselves. Deciding "same control or new" from fingerprints and reading
`check.result` come with the scan.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session as Db

from backend.common.ids import new_id
from backend.common.vocab import ControlKind, ControlState, MappingStatus
from backend.hub.db.models import Control, Crop, Mapping


def now() -> datetime:
    return datetime.now(UTC)


def add_crop(db: Db, kind: str, path: str, w: int = 0, h: int = 0) -> Crop:
    crop = Crop(crop_id=new_id("cr"), kind=kind, path=path, w=w, h=h, received_at=now())
    db.add(crop)
    return crop


def add_control(
    db: Db, kind: ControlKind, state: ControlState, state_confidence: float, actions: list[str], panel_point, spot: str
) -> Control:
    t = now()
    c = Control(
        control_id=new_id("ctl"),
        kind=kind,
        state=state,
        state_confidence=state_confidence,
        actions=actions,
        panel_point=panel_point,
        fingerprint=None,
        crop_id=None,
        seen_spot_id=spot,
        press_spot_id=spot,
        seen_at=t,
        created_at=t,
    )
    db.add(c)
    return c


def add_mapping(
    db: Db,
    control: Control,
    device_id: str | None,
    confidence: float,
    *,
    before: Crop,
    after: Crop,
    device_box: list[float],
    change_score: float,
    device_type_seen: str | None,
    reason: str,
) -> Mapping:
    """A new mapping is Pending until it is checked (C-08)."""
    t = now()
    m = Mapping(
        mapping_id=new_id("mp"),
        control_id=control.control_id,
        device_id=device_id,
        status=MappingStatus.PENDING,
        confidence=confidence,
        method="watch",
        control_before=ControlState.OFF,
        control_after=ControlState.ON,
        device_box=device_box,
        verify_spot_id=control.seen_spot_id,
        change_score=change_score,
        device_type_seen=device_type_seen,
        device_score=confidence if device_id else None,
        before_crop_id=before.crop_id,
        after_crop_id=after.crop_id,
        status_reason=reason,
        created_at=t,
        updated_at=t,
    )
    db.add(m)
    return m


def verify_mapping(db: Db, m: Mapping, device_id: str, confidence: float, reason: str) -> str | None:
    """The check saw which device changed: the mapping points at it and becomes Verified. Returns the device
    it pointed at before, when the guess was wrong."""
    before = m.device_id if m.device_id != device_id else None
    m.device_id, m.confidence, m.status = device_id, confidence, MappingStatus.VERIFIED
    m.status_reason, m.updated_at = reason, now()
    return before
