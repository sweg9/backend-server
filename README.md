# backend-server

The sweg9 backend in two run modes from one repository (FR-02 to FR-07, FR-10 to FR-12). Both run on the laptop at home; nothing leaves the home.

| Mode | Runs as | Does |
|---|---|---|
| `hub` | `uv run backend hub` on the laptop itself (it needs the USB webcam and the Arduino) | Camera, recognition through `ai-engine`, channel adapters, Safety Guard, the hand through the RAL, result checks |
| `server` | A pod on a one-node Kubernetes cluster (k3d) on the same laptop | Mapping Registry, routines, triggers, autonomy policy, the run log, the API for the web app |

Operational logs of both go to Loki and are searched in Grafana, also on the cluster.

The design is on the wiki: [Stack](https://sweg9.github.io/docs/overview/stack), [Database](https://sweg9.github.io/docs/overview/database), [Modules](https://sweg9.github.io/docs/overview/modules), [Hub messages](https://sweg9.github.io/docs/api/hub-messages), [ai-engine interface](https://sweg9.github.io/docs/api/ai-engine).

## Status

A skeleton. Built and tested: the shared names (`common/vocab.py`), every hub message (`common/messages.py`), the run entry with rules L-04, L-05 and L-09 (`common/runlog.py`), the settings files, JSON logs (`common/logs.py`), the database tables and the first migration, the daily backup, the hub's outbox, the channel adapter and RAL interfaces with the `stub` and `script` implementations, and every API route of the spec (each answers `501` until it is built). Every other module has a docstring that says what it will do.

The Dockerfile and the Kubernetes manifests in `deploy/` have not been run yet.

## Run

```bash
uv sync --extra server                     # Python 3.11; add --extra hub for the hub
uv run pytest                              # tests, including the import rules
uv run ruff check . && uv run ruff format --check .
uv run backend hash-password               # prints the value for APP_PASSWORD_HASH
```

On the cluster (needs docker, k3d, kubectl and mkcert):

```bash
export HUB_TOKEN=... APP_PASSWORD_HASH=... GRAFANA_ADMIN_PASSWORD=...
deploy/up.sh                               # cluster, image, secrets, TLS, manifests
HUB_TOKEN=$HUB_TOKEN uv run backend hub    # the hub, outside the cluster
```

The API is at `https://sweg9.localhost/api` and Grafana at `https://grafana.sweg9.localhost`. Data, backups, Loki's files and the hub's logs are in `~/sweg9-data/`, outside the cluster.

## Layout

| Path | Content |
|---|---|
| `src/backend/common/` | Used by both modes: names, hub messages, run entry, settings, ids, logs |
| `src/backend/hub/` | Hub mode. Never imports `server` |
| `src/backend/server/` | Server mode. Never imports `hub` or `ai_engine` |
| `settings/` | `hub.yaml`, `server.yaml`, `risk.yaml`: every starting value (C-12). No secrets (C-10). On the cluster, a ConfigMap |
| `openapi/backend-server.yaml` | The API spec. This repo owns it; copy it to the wiki's `public/api/` when it changes. A test checks that the routes match it |
| `migrations/` | Alembic. Change a table only through a new migration |
| `Dockerfile`, `deploy/` | The server image, the k3d cluster, the manifests (`kubectl apply -k deploy/k8s`), `up.sh` |
| `tests/` | `common/`, `hub/`, `server/`, `contract/`. Mark each test with the requirement it serves: `@pytest.mark.fr("FR-07")` (C-01) |

Secrets come from the environment and Kubernetes Secrets only: `HUB_TOKEN`, `APP_PASSWORD_HASH`, `GRAFANA_ADMIN_PASSWORD`.

## Skills and standards

`skills/` is a git submodule of [harness](https://github.com/sweg9/harness). It holds the shared standards and skills for this project. Claude Code finds the skills through `.claude/skills`.

```bash
git clone --recurse-submodules <this repo>
git submodule update --init            # in a repo that is already cloned
git submodule update --remote skills   # move to the latest skills, then commit
```
