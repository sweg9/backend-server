"""Writes the run entry when a run ends in any way, and ends runs left open by a restart (FR-12, L-01).

A run lives in `open_runs` while it goes on. When it ends, its whole entry (execution-log.md, checked by
RunEntry) goes into the append-only `runs` table and the open row goes, in one transaction.
"""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as Db

from backend.common.runlog import RunEntry
from backend.common.vocab import DeviceAction, RunResult, Trigger
from backend.hub.db.models import Device, OpenRun, Routine, Run
from backend.hub.explain import explain

RESTARTED = "the hub restarted during the run"


def write_run(
    db: Db,
    entry: RunEntry,
    *,
    ended_at: datetime,
    routine_id: str | None,
    mapping_id: str | None,
    situation_id: str | None = None,
) -> Run:
    row = Run(
        run_id=entry.run_id,
        started_at=entry.started_at,
        ended_at=ended_at,
        trigger=entry.trigger,
        routine_id=routine_id,
        device_id=entry.device_id,
        mapping_id=mapping_id,
        decision=entry.decision,
        channel=entry.channel,
        result=entry.result,
        last_crop_id=None,
        situation_id=situation_id,
        entry=entry.model_dump(mode="json"),
    )
    db.add(row)
    db.execute(delete(OpenRun).where(OpenRun.run_id == entry.run_id))
    return row


def end_left_open(db: Db, time_zone: str) -> int:
    """At start: every run still open was cut by a restart, so it ends as cancelled (L-01)."""
    now = datetime.now(UTC)
    rows = db.scalars(select(OpenRun)).all()
    for o in rows:
        device = db.get(Device, o.device_id)
        routine = db.get(Routine, o.routine_id) if o.routine_id else None
        entry = RunEntry(
            run_id=o.run_id,
            trigger=o.trigger,
            started_at=o.started_at,
            intent=None,
            device_id=o.device_id,
            risk=o.risk,
            confidence=o.confidence,
            decision=o.decision,
            channel=None,
            result=RunResult.CANCELLED,
            reason=RESTARTED,
            checks={"main": None, "disagree": False},
            stages=[{"stage": "S7", "status": "ok", "latency_ms": 0}],
            attempts=[],
            explanation=explain(
                device_name=device.name if device else o.device_id,
                action=DeviceAction(o.action),
                trigger=Trigger(o.trigger),
                started_at=o.started_at,
                time_zone=time_zone,
                result=RunResult.CANCELLED,
                channel=None,
                reason=RESTARTED,
                routine_time=routine.time if routine else None,
            ),
        )
        write_run(
            db, entry, ended_at=now, routine_id=o.routine_id, mapping_id=o.mapping_id, situation_id=o.situation_id
        )
    db.commit()
    return len(rows)
