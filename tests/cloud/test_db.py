"""Wiki: Database. The tables are created and their rules hold in SQLite."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.cloud.db.models import Base, Control, Crop, Device, Mapping, Run
from backend.cloud.db.session import make_engine, make_sessionmaker

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def db():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with make_sessionmaker(engine)() as s:
        yield s


def seed(s):
    s.add(Crop(crop_id="cr-1", kind="control", path="a.jpg", w=1, h=1, received_at=NOW))
    s.flush()
    s.add_all(
        [
            Control(
                control_id="ctl_1",
                kind="switch",
                state="off",
                state_confidence=0.9,
                actions=["press"],
                panel_point=None,
                fingerprint=b"\0" * 1536,
                crop_id="cr-1",
                seen_spot_id="s1",
                seen_at=NOW,
                created_at=NOW,
            ),
            Device(
                device_id="dev_1",
                name="light",
                type="light",
                room="living_room",
                risk="low",
                created_at=NOW,
                updated_at=NOW,
            ),
        ]
    )
    s.commit()


def mapping(mid, status="Pending", device_id="dev_1"):
    return Mapping(
        mapping_id=mid,
        control_id="ctl_1",
        device_id=device_id,
        status=status,
        confidence=0.5,
        method="watch",
        control_before="off",
        control_after="on",
        device_box=[0, 0, 1, 1],
        verify_spot_id="s1",
        change_score=0.6,
        before_crop_id="cr-1",
        after_crop_id="cr-1",
        created_at=NOW,
        updated_at=NOW,
    )


def run_row():
    return Run(run_id="r_1", started_at=NOW, ended_at=NOW, trigger="time", decision="act", result="success", entry={})


@pytest.mark.fr("FR-12")
def test_runs_are_append_only(db):
    db.add(run_row())
    db.commit()
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("UPDATE runs SET result = 'failure'"))
    db.rollback()
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("DELETE FROM runs"))


@pytest.mark.fr("FR-02")
def test_one_live_mapping_per_control_and_device(db):
    seed(db)
    db.add(mapping("mp_1", "Rejected"))
    db.add(mapping("mp_2", "Pending"))
    db.commit()
    db.add(mapping("mp_3", "Verified"))
    with pytest.raises(IntegrityError):
        db.commit()


@pytest.mark.fr("FR-02")
def test_device_may_be_missing_only_while_pending(db):
    seed(db)
    db.add(mapping("mp_1", "Pending", device_id=None))
    db.commit()
    db.add(mapping("mp_2", "Verified", device_id=None))
    with pytest.raises(IntegrityError):
        db.commit()


def test_names_are_checked(db):
    db.add(Device(device_id="dev_2", name="x", type="lamp", room="r", risk="low", created_at=NOW, updated_at=NOW))
    with pytest.raises(IntegrityError):
        db.commit()


def test_times_are_stored_as_utc_iso_text(db):
    seed(db)
    assert db.execute(text("SELECT seen_at FROM controls")).scalar() == "2026-11-17T12:00:00.000Z"
