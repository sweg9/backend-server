"""`backend seed-demo`: fills an empty hub database with the demo home in demo_home.json.

Made-up records (devices, controls, mappings, routines, past runs and the uses behind the habits) so
the app has something to show before a real scan. Every past run is a whole run entry checked by
RunEntry (execution-log.md), with its explanation from explain.py, the same as a real run.
"""

import json
import random
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from backend.common.ids import new_id
from backend.common.runlog import RunEntry
from backend.common.vocab import Channel, DeviceAction, RunResult, Trigger
from backend.hub.db.models import ChannelRow, Control, Crop, Device, Mapping, Routine, Run, Use
from backend.hub.explain import explain, in_sentence

NO_PHOTO = "(seed: no photo)"


class NotEmpty(Exception):
    pass


def load_demo_home() -> dict[str, Any]:
    return json.loads(resources.files("backend.hub").joinpath("demo_home.json").read_text("utf-8"))


def _routine_start(hours_ago: float, hhmm: str, t: datetime, tz: ZoneInfo) -> datetime:
    """A time-triggered run starts at its routine's time, on the day `hours_ago` points to."""
    hh, mm = map(int, hhmm.split(":"))
    local = (t - timedelta(hours=hours_ago)).astimezone(tz).replace(hour=hh, minute=mm, second=0, microsecond=0)
    started = local.astimezone(UTC)
    return started - timedelta(days=1) if started > t else started


def _entry(
    p: dict[str, Any], run_id: str, started: datetime, device: dict, mapping: dict | None, routine_time, time_zone: str
) -> RunEntry:
    stages: list[dict[str, Any]] = [{"stage": "S4", "status": "ok", "latency_ms": 900}]
    attempts = []
    for channel, answer in p["attempts"]:
        arm = channel == Channel.ARM
        attempts.append(
            {
                "channel": channel,
                "latency_ms": 6400 if arm else 1800,
                "answer": answer,
                "source": "platform" if channel in (Channel.WIFI, Channel.BT) else "camera",
                **({"gate": "pass"} if arm else {}),
            }
        )
        stages.append(
            {
                "stage": "S6" if arm else "S5",
                "status": "ok" if answer == "yes" else "fail",
                "latency_ms": 6400 if arm else 1800,
                "reason": None if answer == "yes" else f"{channel} answer: {answer}",
            }
        )
    result = RunResult(p["result"])
    if result == RunResult.CANCELLED:  # the gate stopped the press (C-04)
        stages.append({"stage": "S6", "status": "skipped", "latency_ms": 300, "reason": p["reason"]})
    stages.append({"stage": "S7", "status": "ok", "latency_ms": 120})
    last = attempts[-1] if attempts else None
    channel = Channel(last["channel"]) if last else None
    return RunEntry(
        run_id=run_id,
        trigger=p["trigger"],
        started_at=started,
        intent=f"turn {p['action']} the {in_sentence(device['name'])}",
        device_id=device["id"],
        risk=device["risk"],
        confidence=round(mapping["confidence"] * 0.95, 2) if mapping else 0.5,
        decision="act",
        channel=channel,
        result=result,
        reason=p.get("reason"),
        checks={
            "source": last["source"] if last else None,
            "main": last["answer"] if last and result != RunResult.CANCELLED else None,
            "second": None if not last or last["source"] == "platform" else {"room_changed": last["answer"]},
            "disagree": False,
        },
        stages=stages,
        attempts=attempts,
        explanation=explain(
            device_name=device["name"],
            action=DeviceAction(p["action"]),
            trigger=Trigger(p["trigger"]),
            started_at=started,
            time_zone=time_zone,
            result=result,
            channel=channel,
            reason=p.get("reason"),
            routine_time=routine_time,
        ),
    )


def seed_demo(db: Db, time_zone: str, now: datetime | None = None) -> dict[str, int]:
    """Adds the demo home. Refuses a database that already has devices or controls."""
    if db.scalar(select(Device.device_id).limit(1)) or db.scalar(select(Control.control_id).limit(1)):
        raise NotEmpty("the hub's database already has records")
    home = load_demo_home()
    t = now or datetime.now(UTC)
    tz = ZoneInfo(time_zone)
    rng = random.Random(9)

    before = Crop(crop_id=new_id("cr"), kind="device_before", path=NO_PHOTO, w=0, h=0, received_at=t)
    after = Crop(crop_id=new_id("cr"), kind="device_after", path=NO_PHOTO, w=0, h=0, received_at=t)
    db.add_all([before, after])

    devices: dict[str, dict[str, Any]] = {}
    channel_rows: list[ChannelRow] = []
    for i, d in enumerate(home["devices"]):
        created = t - timedelta(days=10, minutes=-i)
        devices[d["key"]] = {**d, "id": new_id("dev")}
        db.add(
            Device(
                device_id=devices[d["key"]]["id"],
                name=d["name"],
                type=d["type"],
                room=d["room"],
                risk=d["risk"],
                created_at=created,
                updated_at=created,
            )
        )
        for n, ch in enumerate(d["channels"]):
            channel_rows.append(
                ChannelRow(
                    device_id=devices[d["key"]]["id"],
                    channel=ch,
                    address=None,
                    success_score=round(0.8 - 0.1 * n, 2),
                    latency_ms=1200,
                    tries=0,
                    updated_at=created,
                )
            )

    db.flush()  # the tables have no ORM relationships, so parents are flushed before their children
    db.add_all(channel_rows)

    controls: dict[str, dict[str, Any]] = {}
    for i, c in enumerate(home["controls"]):
        created = t - timedelta(days=10, minutes=-i)
        controls[c["key"]] = {**c, "id": new_id("ctl")}
        db.add(
            Control(
                control_id=controls[c["key"]]["id"],
                kind=c["kind"],
                state=c["state"],
                state_confidence=0.9,
                actions=c["actions"],
                panel_point=c["panel_point"],
                fingerprint=None,
                crop_id=None,
                seen_spot_id=c["spot"],
                press_spot_id=c["spot"],
                seen_at=t - timedelta(days=2),
                created_at=created,
            )
        )
    db.flush()

    mappings: dict[str, dict[str, Any]] = {}
    for i, m in enumerate(home["mappings"]):
        created = t - timedelta(days=9, minutes=-i)
        mappings[m["key"]] = {**m, "id": new_id("mp")}
        c = controls[m["control"]]
        db.add(
            Mapping(
                mapping_id=mappings[m["key"]]["id"],
                control_id=c["id"],
                device_id=devices[m["device"]]["id"] if m["device"] else None,
                status=m["status"],
                confidence=m["confidence"],
                method="test",
                control_before="off",
                control_after="on",
                device_box=[0.3, 0.2, 0.6, 0.7],
                verify_spot_id=c["spot"],
                change_score=m["confidence"],
                device_type_seen=devices[m["device"]]["type"] if m["device"] else None,
                device_score=m["confidence"] if m["device"] else None,
                before_crop_id=before.crop_id,
                after_crop_id=after.crop_id,
                status_reason="demo seed",
                created_at=created,
                updated_at=created,
            )
        )
    db.flush()

    routines: dict[str, dict[str, Any]] = {}
    for r in home["routines"]:
        routines[r["key"]] = {**r, "id": new_id("rt")}
        db.add(
            Routine(
                routine_id=routines[r["key"]]["id"],
                mapping_id=mappings[r["mapping"]]["id"],
                action=r["action"],
                time=r["time"],
                source=r["source"],
                independence=r["independence"],
                priority=r["priority"],
                reject_streak=0,
                created_at=t - timedelta(days=8),
                updated_at=t - timedelta(days=8),
            )
        )
        # The uses a habit was found from: one a day near its time, for the last 7 days (FR-04).
        if r["source"] == "habit":
            m = mappings[r["mapping"]]
            for day in range(1, 8):
                at = _routine_start(24 * day, r["time"], t, tz) + timedelta(minutes=rng.randint(-15, 15))
                db.add(
                    Use(
                        use_id=new_id("use"),
                        device_id=devices[m["device"]]["id"],
                        control_id=controls[m["control"]]["id"],
                        action=r["action"],
                        at=at,
                        source="seed",
                    )
                )
    db.flush()

    by_device_mapping = {m["device"]: m for m in mappings.values() if m["device"] and m["status"] == "Verified"}
    runs = sorted(home["past_runs"], key=lambda p: -p["hours_ago"])
    for p in runs:
        routine = routines.get(p.get("routine") or "")
        started = t - timedelta(hours=p["hours_ago"])
        if routine and p["trigger"] == "time":
            started = _routine_start(p["hours_ago"], routine["time"], t, tz)
        run_id = new_id("r")
        device = devices[p["device"]]
        mapping = by_device_mapping.get(p["device"])
        entry = _entry(p, run_id, started, device, mapping, routine["time"] if routine else None, time_zone)
        db.add(
            Run(
                run_id=run_id,
                started_at=started,
                ended_at=started + timedelta(milliseconds=sum(s.latency_ms for s in entry.stages)),
                trigger=p["trigger"],
                routine_id=routine["id"] if routine else None,
                device_id=device["id"],
                mapping_id=mapping["id"] if mapping else None,
                decision="act",
                channel=entry.channel,
                result=entry.result,
                last_crop_id=None,
                entry=entry.model_dump(mode="json"),
            )
        )
    db.commit()
    return {
        "devices": len(devices),
        "controls": len(controls),
        "mappings": len(mappings),
        "routines": len(routines),
        "runs": len(runs),
    }
