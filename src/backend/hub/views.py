"""The hub's records in the shapes of openapi/backend-server.yaml."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session as Db

from backend.hub.db.models import Control, Device, Mapping, Routine, Run, RunChoice, Situation

RUN_FIELDS = ("run_id", "trigger", "intent", "device_id", "confidence", "channel", "result", "reason", "explanation")


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def control_view(c: Control) -> dict[str, Any]:
    return {
        "control_id": c.control_id,
        "kind": c.kind,
        "state": c.state,
        "actions": c.actions,
        "position": c.panel_point or [],
        "seen_at": iso(c.seen_at),
    }


def device_view(d: Device, channels: list[str]) -> dict[str, Any]:
    return {
        "device_id": d.device_id,
        "name": d.name,
        "type": d.type,
        "room": d.room,
        "risk": d.risk,
        "channels": channels,
    }


def mapping_view(m: Mapping) -> dict[str, Any]:
    return {
        "mapping_id": m.mapping_id,
        "control_id": m.control_id,
        "device_id": m.device_id,
        "status": m.status,
        "confidence": m.confidence,
    }


def routine_view(r: Routine) -> dict[str, Any]:
    out = {
        "routine_id": r.routine_id,
        "mapping_id": r.mapping_id,
        "action": r.action,
        "source": r.source,
        "independence": r.independence,
        "priority": r.priority,
    }
    if r.time:
        out["time"] = r.time
    return out


def run_view(r: Run, choice: RunChoice | None = None, retry_of: str | None = None) -> dict[str, Any]:
    """The spec's Run: a subset of the whole run entry (execution-log.md) kept in `entry`, with its
    situation, the resident's choice after a failure, and the run it retried."""
    out = {k: r.entry[k] for k in RUN_FIELDS if r.entry.get(k) is not None}
    out["started_at"] = iso(r.started_at)
    out["situation_id"] = r.situation_id
    out["resolution"] = {"choice": choice.choice, "retry_run_id": choice.retry_run_id} if choice else None
    out["retry_of"] = retry_of
    return out


def run_views(db: Db, rows: list[Run]) -> list[dict[str, Any]]:
    """run_view for many runs, with their choices read in one query."""
    ids = [r.run_id for r in rows]
    if not ids:
        return []
    choices = db.scalars(select(RunChoice).where(or_(RunChoice.run_id.in_(ids), RunChoice.retry_run_id.in_(ids))))
    by_run: dict[str, RunChoice] = {}
    retry_of: dict[str, str] = {}
    for c in choices:
        by_run[c.run_id] = c
        if c.retry_run_id:
            retry_of[c.retry_run_id] = c.run_id
    return [run_view(r, by_run.get(r.run_id), retry_of.get(r.run_id)) for r in rows]


def situation_view(s: Situation) -> dict[str, Any]:
    return {
        "situation_id": s.situation_id,
        "kind": s.kind,
        "trigger": s.trigger,
        "summary": s.summary,
        "received_at": iso(s.received_at),
        "status": s.status,
        "plan": s.plan,
    }
