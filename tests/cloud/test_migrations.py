"""Alembic records every schema change: the migrations build the same tables as the models."""

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from backend.cloud.db.models import Base

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def migrated(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        env={**__import__("os").environ, "DATABASE_URL": url},
    )
    return create_engine(url)


def test_migrations_match_the_models(migrated):
    tables = set(inspect(migrated).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)


@pytest.mark.fr("FR-12")
def test_migrated_runs_are_append_only(migrated):
    with migrated.connect() as c:
        triggers = c.execute(text("SELECT name FROM sqlite_master WHERE type = 'trigger'")).scalars().all()
    assert set(triggers) == {"runs_no_update", "runs_no_delete"}
