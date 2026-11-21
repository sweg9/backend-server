# backend-server

The sweg9 backend in two run modes from one repository (FR-01 to FR-12; the model steps of FR-01, FR-02, FR-08 and FR-09 are calls into `ai-engine`).

Since the 2026-11-20 meeting the server runs on AWS and the hub at home:

| Mode | Runs as | Does |
|---|---|---|
| `hub` | `uv run backend hub` on a laptop at home (it needs the USB webcam and the Arduino) | Reader (photos from the robot), the records in SQLite, Safety Guard, Dashboard (Streamlit), channel adapters, the hand through the RAL |
| `server` | A pod on k3s on one AWS EC2 instance | Relays the web app's calls to the hub and the hub's requests to the enclave, puts sealed photos in S3, monitoring. No database |

The models (`ai-engine`: YOLO11n, Linker, MobileNet) and the autonomy policy run in a Nitro Enclave on the same EC2 instance, reached over vsock.
- No video leaves the home. The hub seals each single photo a step needs before it leaves the home.
- Only the attested enclave can get the KMS key that opens it.
- S3 keeps the photos sealed.

Operational logs of both go to Loki and are searched in Grafana, on the cluster.

The design is on the wiki: [Stack](https://sweg9.github.io/docs/overview/stack), [Database](https://sweg9.github.io/docs/overview/database), [Modules](https://sweg9.github.io/docs/overview/modules), [Hub messages](https://sweg9.github.io/docs/api/hub-messages), [ai-engine interface](https://sweg9.github.io/docs/api/ai-engine).

## Status

A skeleton. Built and tested: the shared names (`common/vocab.py`), every hub message (`common/messages.py`), the run entry with rules L-04, L-05 and L-09 (`common/runlog.py`), the settings files, JSON logs (`common/logs.py`), the database tables and the first migration, the daily backup, the hub's outbox, the channel adapter and RAL interfaces with the `stub` and `script` implementations, and every API route of the spec (each answers `501` until it is built). Every other module has a docstring that says what it will do.

The Dockerfile and the Kubernetes manifests in `deploy/` have not been run yet.

The code still follows the 2026-11-17 split, where the server kept the database and the hub ran the models on one laptop. It moves to the new split as it is built. The target layout is on the wiki ([Modules](https://sweg9.github.io/docs/overview/modules)), and `deploy/` and `settings/` already describe the target.

## Run

```bash
uv sync --extra server                     # Python 3.11; add --extra hub for the hub
uv run pytest                              # tests, including the import rules
uv run ruff check . && uv run ruff format --check .
uv run backend hash-password               # prints the value for APP_PASSWORD_HASH
```

On AWS: set up the EC2 instance as `deploy/README.md` says, then `kubectl apply -k deploy/k8s` there. The hub runs on the laptop at home:

```bash
HUB_TOKEN=... APP_PASSWORD_HASH=... uv run backend hub
```

The API and the hub's WebSocket are at `https://<domain>/api`, and Grafana at `https://grafana.<domain>`; the domain is still open.

## Layout

| Path | Content |
|---|---|
| `src/backend/common/` | Used by both modes: names, hub messages, run entry, settings, ids, logs |
| `src/backend/hub/` | Hub mode. Never imports `server` |
| `src/backend/server/` | Server mode. Never imports `hub` or `ai_engine` |
| `settings/` | `hub.yaml`, `server.yaml`, `risk.yaml`: every starting value (C-12). No secrets (C-10). On the cluster, a ConfigMap |
| `openapi/backend-server.yaml` | The API spec. This repo owns it; copy it to the wiki's `public/api/` when it changes. A test checks that the routes match it |
| `migrations/` | Alembic. Change a table only through a new migration |
| `Dockerfile`, `deploy/` | The server image, the manifests (`kubectl apply -k deploy/k8s`), and how to set up the EC2 instance (`deploy/README.md`) |
| `tests/` | `common/`, `hub/`, `server/`, `contract/`. Mark each test with the requirement it serves: `@pytest.mark.fr("FR-07")` (C-01) |

Secrets come from the environment and Kubernetes Secrets only: `HUB_TOKEN`, `APP_PASSWORD_HASH` (the hub), `GRAFANA_ADMIN_PASSWORD`.

## Skills and standards

`skills/` is a git submodule of [harness](https://github.com/sweg9/harness). It holds the shared standards and skills for this project. Claude Code finds the skills through `.claude/skills`.

```bash
git clone --recurse-submodules <this repo>
git submodule update --init            # in a repo that is already cloned
git submodule update --remote skills   # move to the latest skills, then commit
```
