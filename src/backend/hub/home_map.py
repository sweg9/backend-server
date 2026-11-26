"""`GET /map`: the home as the Dashboard draws it (HomeLayout in openapi/backend-server.yaml, FR-09).

The plan (rooms, walls, furniture) and where each spot and device stands come from home_map.json; which
controls and devices exist comes from the records. A control stands at its spot, so a control found later
(demo scenario 1) shows up without editing the file. Several controls at one spot sit side by side.
Numbers only, never a photo (C-05).
"""

import json
from functools import cache
from importlib import resources
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from backend.hub.db.models import Control, Device

SIDE_BY_SIDE_CM = 14  # between controls at one spot, along the wall


@cache
def plan() -> dict[str, Any]:
    return json.loads(resources.files("backend.hub").joinpath("home_map.json").read_text("utf-8"))


def home_layout(db: Db) -> dict[str, Any]:
    p = plan()
    spots: dict[str, dict] = p["spots"]
    controls, seen = [], {}
    for c in db.scalars(select(Control).order_by(Control.created_at, Control.control_id)):
        spot_id = c.press_spot_id or c.seen_spot_id
        spot = spots.get(spot_id)
        if spot is None:  # a spot the plan does not know yet: listed, not drawn
            continue
        n = seen[spot_id] = seen.get(spot_id, -1) + 1
        at = {"x": spot["at"]["x"] + n * SIDE_BY_SIDE_CM, "y": spot["at"]["y"]}
        controls.append({"control_id": c.control_id, "at": at, "press_spot": spot["press_spot"], "spot_id": spot_id})
    devices = [
        {"device_id": d.device_id, "at": p["devices"][d.name]}
        for d in db.scalars(select(Device).order_by(Device.created_at, Device.device_id))
        if d.name in p["devices"]
    ]
    return {
        "rooms": p["rooms"],
        "controls": controls,
        "devices": devices,
        "dock": p["dock"],
        "walls": p["walls"],
        "furniture": p["furniture"],
        "ceiling": p["ceiling"],
    }
