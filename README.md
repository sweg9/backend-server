# backend-server

The sweg9 backend in two run modes from one repository (FR-02 to FR-07, FR-10 to FR-12):

| Mode | Runs on | Does |
|---|---|---|
| `hub` | The laptop at home | Camera, recognition through `ai-engine`, channel adapters, Safety Guard, the hand through the RAL, result checks |
| `cloud` | AWS EC2 | Mapping Registry, routines, triggers, autonomy policy, the run log, the API for the web app |

The design is on the wiki: [Stack](https://sweg9.github.io/docs/overview/stack), [Database](https://sweg9.github.io/docs/overview/database), [Modules](https://sweg9.github.io/docs/overview/modules), [Hub messages](https://sweg9.github.io/docs/api/hub-messages), [ai-engine interface](https://sweg9.github.io/docs/api/ai-engine).

## Status

A skeleton. Built and tested: the shared names (`common/vocab.py`), every hub message (`common/messages.py`), the run entry with rules L-04, L-05 and L-09 (`common/runlog.py`), the settings files, the database tables and the first migration, the hub's outbox, the channel adapter and RAL interfaces with the `stub` and `script` implementations, and every API route of the spec (each answers `501` until it is built). Every other module has a docstring that says what it will do.

## Run

```bash
uv sync --extra cloud                      # Python 3.11; add --extra hub on the laptop
uv run pytest                              # tests, including the import rules
uv run ruff check . && uv run ruff format --check .
DATABASE_URL=sqlite:///data/cloud/app.db uv run alembic upgrade head
uv run backend cloud                       # the cloud API on http://127.0.0.1:8000 (one worker)
uv run backend hash-password               # prints the value for APP_PASSWORD_HASH
```

## Layout

| Path | Content |
|---|---|
| `src/backend/common/` | Used by both modes: names, hub messages, run entry, settings, ids |
| `src/backend/hub/` | Hub mode. Never imports `cloud` |
| `src/backend/cloud/` | Cloud mode. Never imports `hub` or `ai_engine` |
| `settings/` | `hub.yaml`, `cloud.yaml`, `risk.yaml`: every starting value (C-12). No secrets (C-10) |
| `openapi/backend-server.yaml` | The API spec. This repo owns it; copy it to the wiki's `public/api/` when it changes. A test checks that the routes match it |
| `migrations/` | Alembic. Change a table only through a new migration |
| `tests/` | `common/`, `hub/`, `cloud/`, `contract/`. Mark each test with the requirement it serves: `@pytest.mark.fr("FR-07")` (C-01) |

Secrets come from the environment only: `HUB_TOKEN` (hub and cloud) and `APP_PASSWORD_HASH` (cloud).

## Skills and standards

`skills/` is a git submodule of [harness](https://github.com/sweg9/harness). It holds the shared standards and skills for this project. Claude Code finds the skills through `.claude/skills`.

```bash
git clone --recurse-submodules <this repo>
git submodule update --init            # in a repo that is already cloned
git submodule update --remote skills   # move to the latest skills, then commit
```
