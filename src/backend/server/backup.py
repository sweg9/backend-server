"""Daily backup of the server's data (wiki: Stack > Backups). Run by the `backup` CronJob.

sqlite3's backup API gives a consistent copy while the server keeps writing in WAL mode.
The crops are copied next to it. Backups older than `backup_keep_days` are deleted.
"""

import logging
import shutil
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


def sqlite_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("only SQLite databases are backed up this way")
    return Path(database_url.removeprefix("sqlite:///"))


def run_backup(database_url: str, crop_dir: str, backup_dir: str, keep_days: int, today: date | None = None) -> Path:
    today = today or datetime.now(UTC).date()
    target = Path(backup_dir) / today.isoformat()
    target.mkdir(parents=True, exist_ok=True)

    src = sqlite3.connect(sqlite_path(database_url))
    dst = sqlite3.connect(target / "app.db")
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    crops = Path(crop_dir)
    if crops.exists():
        shutil.copytree(crops, target / "crops", dirs_exist_ok=True)

    oldest = today - timedelta(days=keep_days - 1)
    for old in Path(backup_dir).iterdir():
        try:
            day = date.fromisoformat(old.name)
        except ValueError:
            continue  # not a backup folder; leave it alone
        if day < oldest:
            shutil.rmtree(old)
            log.info("deleted old backup %s", old.name)
    log.info("backup written to %s", target)
    return target
