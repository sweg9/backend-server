"""Hub messages > Delivery: the outbox keeps confirmed messages until their ack."""

from datetime import UTC, datetime, timedelta

import pytest

from backend.common.messages import Envelope
from backend.hub.outbox import Outbox


def msg(i, type_="scan.done", age_days=0):
    return Envelope(
        type=type_,
        id=f"m-{i}",
        run_id="r-1" if type_.startswith("run.") else None,
        sent_at=datetime.now(UTC) - timedelta(days=age_days),
        payload={"scan_id": "sc-1", "found": 0, "skipped": 0, "reason": None},
    )


def test_kept_until_ack_and_survives_restart(tmp_path):
    box = Outbox(tmp_path / "outbox.jsonl", max_age_days=7)
    box.add(msg(1))
    box.add(msg(2))
    box.ack(["m-1"])
    again = Outbox(tmp_path / "outbox.jsonl", max_age_days=7)
    assert [m.id for m in again.pending()] == ["m-2"]


def test_progress_does_not_go_in_the_outbox(tmp_path):
    with pytest.raises(ValueError):
        Outbox(tmp_path / "o.jsonl", 7).add(msg(1, "run.progress"))


def test_messages_older_than_the_server_remembers_are_dropped(tmp_path):
    box = Outbox(tmp_path / "o.jsonl", max_age_days=7)
    box.add(msg(1, age_days=8))
    box.add(msg(2))
    assert [m.id for m in box.pending()] == ["m-2"]
