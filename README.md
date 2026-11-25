# backend-server

The sweg9 backend in two run modes from one repository (FR-01 to FR-12; the model steps of FR-01, FR-02, FR-08 and FR-09 are calls into `ai-engine`).

Since the 2026-11-20 meeting the server runs on AWS and the hub at home:

| Mode | Runs as | Does |
|---|---|---|
| `hub` | `uv run backend hub` on a laptop at home (it needs the USB webcam and the Arduino) | Reader (photos from the robot), Encryption (seals each photo), the records in SQLite, Safety Guard, Streamlit (read-only screen), channel adapters, the hand through the RAL |
| `server` | A pod on k3s on one AWS EC2 instance | Relays the Dashboard's calls to the hub and the hub's requests to the enclave, puts sealed photos in S3, monitoring. No database |

The models (`ai-engine`: YOLO11n, Linker, MobileNet) and the autonomy policy run in a Nitro Enclave on the same EC2 instance, reached over vsock.
- No video leaves the home. The hub seals each single photo a step needs before it leaves the home.
- Only the attested enclave can get the KMS key that opens it.
- S3 keeps the photos sealed.

Operational logs of both go to Loki and are searched in Grafana, on the cluster.

The design is on the wiki: [Stack](https://sweg9.github.io/docs/overview/stack), [Database](https://sweg9.github.io/docs/overview/database), [Modules](https://sweg9.github.io/docs/overview/modules), [Hub messages](https://sweg9.github.io/docs/api/hub-messages), [ai-engine interface](https://sweg9.github.io/docs/api/ai-engine).

## Status

A skeleton of the 2026-11-20 split ([Modules](https://sweg9.github.io/docs/overview/modules)). Built and tested:

- **common:** the shared names (`vocab.py`), every hub message of contract version 2 (`messages.py`), the run entry with rules L-04, L-05 and L-09 (`runlog.py`), the settings files, JSON logs (`logs.py`).
- **hub:** the database tables and their migrations, the daily backup, the channel adapter and RAL interfaces with the `stub` and `script` implementations, the link to the server (`link.py`: token, reconnect, heartbeat, stop first), every route of the spec answered from the database with sign-in and sessions (`app.py`), the explanation templates (`explain.py`), `backend seed-demo` (`seed.py`, `demo_home.json`), the orchestrator (`orchestrator.py`: plan, wireless first, visit order, press after the gate, one more look after unsure, retry or leave kept in `run_choices`, move orders, emergency stop), the run log (`runs.py`), situations, and the demo scenarios as a trigger source (`scenario.py`, `--demo-scenarios`).
- **server:** every API route of the spec, relayed to the hub (`503` while the hub is offline), the login limit, the emergency stop that waits for `stop.done`, and the hub's WebSocket, which hands the hub's requests to the enclave.
- **enclave:** the autonomy policy (`decide`, FR-06).
- **sealing (C-05):** the hub seals a photo (`hub/seal.py`) and the enclave opens it (`enclave/unseal.py`), in the shared format of `common/sealing.py`. See [Sealed photos](#sealed-photos).

Every other module has a docstring that says what it will do: the hub's scan and its Reader (`camera.py`), the time and sensor triggers, the real answers over the link (enclave decisions, camera checks, the safety gate), the vsock link and the enclave program. Until those answers are built, the orchestrator decides on the safe side (only suggest, no press); a demo scenario gives fixed answers instead. The Dockerfile and the Kubernetes manifests in `deploy/` have not been run yet.

## Sealed photos

The architecture of 2026-11-25: **Hub: Reader → Encryption → (HTTPS) → server → (vsock) → Enclave: Decryption → ML Engine → Policy.** No photo leaves the home unsealed, and only the attested enclave opens one (C-05; wiki: [Hub messages > Sealed photos](https://sweg9.github.io/docs/api/hub-messages#sealed-photos)). The server and S3 see only ciphertext.

| Step | Where | What |
|---|---|---|
| 1. A new data key | `hub/seal.py` `KmsDataKeys` | KMS `GenerateDataKey` (AES_256) with `kms_key_id` from `settings/hub.yaml`: the plain key, and the same key encrypted by KMS |
| 2. Encryption | `hub/seal.py` `seal()` | AES-256-GCM, a random 96-bit nonce, and associated data `nova-seal-v1\|photo_id\|WxH` (`common/sealing.py`), so a photo moved to another id or size does not open. The plain key is dropped |
| 3. Sent | `Sealed` in `common/messages.py` | `photo_id`, `key` (KMS-encrypted), `nonce`, `data`, `w`, `h`, base64. The server passes it on over vsock and puts it in S3 as it is |
| 4. The key, only for this enclave | `enclave/unseal.py` `KmstoolKeys` | KMS Decrypt with the enclave's attestation document, through AWS's `kmstool_enclave_cli` and the parent's vsock proxy. The key policy allows it only for the published image (PCRs) |
| 5. Decryption | `enclave/unseal.py` `unseal()`, `opener()` | AES-256-GCM checks the tag and the associated data, then the JPEG becomes the frame `handlers.py` gives the ML Engine. Only answers leave (A-07) |

**Built and tested** (`tests/contract/test_sealing.py`, with a fake KMS that releases keys only to the "attested" enclave): the round trip from the hub to the enclave; only ciphertext in the message; a new key and nonce per photo; a changed, moved or resized photo does not open; another key or another enclave image cannot open it; the size must match the opened photo; no `kms_key_id` means no photo leaves the home.

**Not run yet** against AWS: `KmsDataKeys` (boto3) and `KmstoolKeys` (`kmstool_enclave_cli`). To go on:

1. **KMS key.** Create a symmetric key; put its id or alias in `settings/hub.yaml` as `kms_key_id`. Its key policy lets the hub's IAM user call `GenerateDataKey` only, and allows `Decrypt` only with the condition `kms:RecipientAttestation:ImageSha384` (PCR0) of the published enclave image. Put the policy in `deploy/README.md`; it changes with every new enclave image.
2. **The hub's AWS credentials.** An IAM user (or role) for the hub at home with `kms:GenerateDataKey` on that key only, from the environment, never committed (C-10).
3. **The Reader** (`hub/camera.py`): take one photo, at most `photo_max_side` px, encode JPEG, give it a `photo_id` with `new_id("ph")` (`ph_<ULID>`; the prefix still has to be added to the wiki's Database page), call `seal(jpeg, photo_id=..., w=..., h=..., keys=data_keys(settings))`. On `SealError` the step goes on the safe side and nothing is sent.
4. **The enclave program** (`enclave/main.py`): build the enclave image with `kmstool_enclave_cli` at `/app/kmstool_enclave_cli`; get temporary credentials from the parent over vsock; `Handlers(engine, settings, risk, opener(KmstoolKeys(region, proxy_port, credentials)))`. Run `vsock-proxy` for KMS on the parent (`ml-engine` pod).
5. **A test on AWS:** seal on a laptop with the real key, open in the enclave, and check that the parent instance cannot decrypt the key (KMS refuses without the attestation).

### Security notes

> **Notice.** Sealing is tested only with a fake KMS; nothing here has run against AWS KMS or in a Nitro Enclave yet. Do not rely on it to protect real photos until step 5 above has passed.

What sealing protects, and what it does not:

| | |
|---|---|
| **Protected** | The photo's content from the server, the EC2 instance, S3, the network and anyone with the AWS account but not the enclave image: they see only ciphertext, and KMS opens the data key only for the attested image (C-05) |
| **Protected** | Swapping or moving: a sealed photo opens only under its own `photo_id` and size (GCM associated data), and a changed byte makes it fail |
| **Not protected** | The hub itself: it holds the photo before sealing. A hub laptop that is taken over sees what the camera sees. Keep it updated and on the home network only |
| **Not protected** | Metadata: the server sees `photo_id`, the size, when a photo is sent and which step asked for it. Logs refer to a photo only by `photo_id` (L-07) |
| **Not protected** | The plain data key in the hub's memory: Python cannot wipe it; `seal()` drops it at once |

Rules for everyone who works on this:

- **Never commit credentials** (C-10): AWS keys, `HUB_TOKEN`, `APP_PASSWORD_HASH`, a KMS key's policy with account ids. They come from the environment or Kubernetes Secrets only. `kms_key_id` in `settings/hub.yaml` is an id, not a secret.
- **Never log or print** a `Sealed` message's `key`, `nonce` or `data`, a plain data key, or an opened photo. Log `photo_id` only.
- **Never open sealed photos outside the enclave**, and never for training (C-05, decision of 2026-11-24). There is no "debug" path that unseals on the server or a laptop, and none should be added.
- **Never reuse a data key or a nonce.** `seal()` asks KMS for a new key per photo; keep it that way.
- **The KMS key policy pins the enclave image** (`kms:RecipientAttestation:ImageSha384`, PCR0). A new enclave image changes it: update the policy with the image, never widen it to "any image".
- **A change to the format** (`common/sealing.py`) changes `FORMAT` (`nova-seal-v1`), so an old enclave refuses new photos instead of misreading them.

**For the team to decide:** whether the hub calls KMS for every photo (now, as the wiki says; the architecture figure has no Hub → KMS arrow yet) or seals with a KMS asymmetric public key fetched once, so the hub needs no AWS credentials at home.

## Run

```bash
uv sync --extra server --extra hub        # Python 3.11
uv run pytest                              # tests, including the import rules
uv run ruff check . && uv run ruff format --check .
uv run backend hash-password               # prints the value for APP_PASSWORD_HASH
```

The server and the hub on one machine, with the demo home (for the Dashboard's development):

```bash
export HUB_TOKEN=dev APP_PASSWORD_HASH='<from hash-password>'
uv run backend seed-demo                   # once; delete data/hub/app.db to start over
uv run backend server --port 8000
uv run backend hub --stdout --demo-scenarios --server-url ws://127.0.0.1:8000/ws/hub
```

With `--demo-scenarios`, `POST /scenarios/leave_home/start` (demo scenario 3) or `new_control` (scenario 1) plays fixed answers through the real orchestrator; watch it on `/ws/app`. Without the flag those routes answer 404.

The session cookie is `Secure` and `SameSite=Strict`, so the Dashboard calls the server through its dev server's proxy (for Vite: `server.proxy` sends `/api` to `http://127.0.0.1:8000` and strips `/api`, like the ingress). Browsers accept `Secure` cookies on `http://localhost`.

The same pair runs on a shared dev instance on AWS, kept stopped when unused: [`deploy/dev-ec2/`](deploy/dev-ec2/README.md).

On AWS: set up the EC2 instance as `deploy/README.md` says, then `kubectl apply -k deploy/k8s` there. The hub runs on the laptop at home:

```bash
HUB_TOKEN=... APP_PASSWORD_HASH=... uv run backend hub
```

The API and the hub's WebSocket are at `https://<domain>/api`, and Grafana at `https://grafana.<domain>`; the domain is still open.

## Layout

| Path | Content |
|---|---|
| `src/backend/common/` | Used by every mode: names, hub messages, run entry, settings, ids, logs |
| `src/backend/hub/` | Hub mode, at home: the database, the runs, the hardware. Never imports `server`, `enclave` or `ai_engine` |
| `src/backend/server/` | Server mode, on AWS: the relay. Never imports `hub`, `enclave` or `ai_engine` |
| `src/backend/enclave/` | Enclave mode, in the Nitro Enclave: the models through `ai_engine` and the autonomy policy. Never imports `hub` or `server` |
| `settings/` | `hub.yaml`, `server.yaml`, `risk.yaml`: every starting value (C-12). No secrets (C-10). On the cluster, a ConfigMap |
| `openapi/backend-server.yaml` | The API spec. This repo owns it; copy it to the wiki's `public/api/` when it changes. A test checks that the routes match it |
| `openapi/dashboard-proposed.yaml` | Routes the dashboard asks for that the spec does not have yet. Not served; adopted routes move into the spec |
| `migrations/` | Alembic, for the hub's database. Change a table only through a new migration |
| `Dockerfile`, `deploy/` | The server image, the manifests (`kubectl apply -k deploy/k8s`), and how to set up the EC2 instance (`deploy/README.md`) |
| `tests/` | `common/`, `hub/`, `server/`, `enclave/`, `contract/`. Mark each test with the requirement it serves: `@pytest.mark.fr("FR-07")` (C-01) |

Secrets come from the environment and Kubernetes Secrets only: `HUB_TOKEN`, `APP_PASSWORD_HASH` (the hub), `GRAFANA_ADMIN_PASSWORD`.

## Skills and standards

`skills/` is a git submodule of [harness](https://github.com/sweg9/harness). It holds the shared standards and skills for this project. Claude Code finds the skills through `.claude/skills`.

```bash
git clone --recurse-submodules <this repo>
git submodule update --init            # in a repo that is already cloned
git submodule update --remote skills   # move to the latest skills, then commit
```
