"""Stack > Backups: a consistent copy of the database and the crops, kept for a set number of days."""

import sqlite3
from datetime import date

from backend.hub.backup import run_backup


def test_backup_copies_db_and_crops_and_prunes(tmp_path):
    db = tmp_path / "app.db"
    with sqlite3.connect(db) as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("CREATE TABLE t (x)")
        c.execute("INSERT INTO t VALUES (1)")
    crops = tmp_path / "crops"
    (crops / "2026").mkdir(parents=True)
    (crops / "2026" / "cr-1.jpg").write_bytes(b"jpg")
    backups = tmp_path / "backups"
    (backups / "2026-11-01").mkdir(parents=True)  # older than 7 days: deleted
    (backups / "notes").mkdir()  # not a backup: left alone

    out = run_backup(f"sqlite:///{db}", str(crops), str(backups), keep_days=7, today=date(2026, 11, 17))

    with sqlite3.connect(out / "app.db") as c:
        assert c.execute("SELECT x FROM t").fetchall() == [(1,)]
    assert (out / "crops" / "2026" / "cr-1.jpg").read_bytes() == b"jpg"
    assert sorted(p.name for p in backups.iterdir()) == ["2026-11-17", "notes"]
