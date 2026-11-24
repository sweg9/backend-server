"""The resident's settings kept in `app_state`."""

from typing import Any

from sqlalchemy.orm import Session as Db

from backend.hub.db.models import AppState

PROACTIVE_KEY = "proactive"


def proactive(db: Db) -> dict[str, Any]:
    """The pause setting (FR-06): not paused until the resident pauses."""
    row = db.get(AppState, PROACTIVE_KEY)
    return row.value if row is not None else {"paused": False}
