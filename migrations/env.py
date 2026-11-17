"""Alembic environment. The database URL comes from settings/cloud.yaml, or DATABASE_URL if set."""

import os
from pathlib import Path

from alembic import context

from backend.cloud.db.models import Base
from backend.cloud.db.session import make_engine
from backend.common.settings import CloudSettings, load

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    return os.environ.get("DATABASE_URL") or load(CloudSettings, "cloud.yaml").database_url


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = database_url()
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    with make_engine(url).connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
