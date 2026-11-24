"""The hub's records in the shapes of openapi/backend-server.yaml."""

from datetime import UTC, datetime
from typing import Any

from backend.hub.db.models import Control, Device, Mapping, Routine, Run, Situation

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


def run_view(r: Run) -> dict[str, Any]:
    """The spec's Run: a subset of the whole run entry (execution-log.md) kept in `entry`."""
    out = {k: r.entry[k] for k in RUN_FIELDS if r.entry.get(k) is not None}
    out["started_at"] = iso(r.started_at)
    return out


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
