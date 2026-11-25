"""Settings files (C-12). Every number the code compares against lives in settings/*.yaml.

A missing or unknown key stops the start: the models forbid extra keys.
"""

from pathlib import Path
from typing import Literal, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict

from backend.common.vocab import DeviceType, HandOption, Independence, Risk

REPO = Path(__file__).resolve().parents[3]
SETTINGS_DIR = REPO / "settings"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Actuator(Strict):
    id: str
    faces: tuple[float, float, float, float]  # box in the image the actuator faces


class HubSettings(Strict):
    """settings/hub.yaml: the hub keeps the records and runs the runs (wiki: Stack)."""

    server_url: str
    kms_key_id: str | None  # C-05
    hand: HandOption
    serial_port: str | None
    camera_index: int
    photo_max_side: int
    log_file: str
    log_max_mb: int
    log_keep_files: int
    spots: dict[str, str]  # spot_id -> room
    actuators: list[Actuator]
    # the records
    database_url: str
    crop_dir: str
    backup_dir: str
    backup_keep_days: int
    backup_at: str
    time_zone: str
    session_days: int
    uses_keep_days: int
    crops_unreferenced_keep_days: int
    # time limits for the server's answers (wiki: Hub messages)
    heartbeat_s: float
    recognize_timeout_s: float
    check_timeout_s: float
    refind_timeout_s: float
    people_timeout_s: float  # FR-10
    decide_timeout_s: float  # FR-05
    # runs
    duplicate_trigger_s: float  # FR-05
    channel_timeout_s: float  # FR-07
    effect_wait_s: float  # FR-08
    unsure_recheck_s: float  # FR-08
    max_press_tries: int  # FR-11
    # habits and learning
    watch_interval_s: float  # FR-04 uses
    watch_quiet_s: float
    habit_window_min: int  # FR-04
    habit_min_days: int
    habit_lookback_days: int
    habit_run_at: str
    habit_independence: Independence
    habit_removed_block_days: int
    demote_after_rejections: int  # FR-04
    channel_newest_weight: float  # FR-12
    link_confidence_step: float  # FR-12
    stale_below: float
    fingerprint_same_control: float  # FR-01


class ServerSettings(Strict):
    """settings/server.yaml: the server on AWS relays and keeps no records."""

    enclave_transport: Literal["vsock", "tcp"]
    enclave_cid: int
    enclave_host: str | None
    enclave_port: int
    enclave_timeout_s: float
    enclave_init_timeout_s: float
    engine_settings_file: str
    aws_credentials: Literal["instance_role", "none"]
    credentials_refresh_before_s: float
    s3_bucket: str | None
    hub_offline_after_missed: int
    app_timeout_s: float
    stop_timeout_s: float
    login_max_failures: int
    login_window_s: float


class EnclaveSettings(Strict):
    """settings/enclave.yaml: the enclave program's limits, passed to it over vsock at start."""

    kmstool_timeout_s: float  # C-05: one KMS Decrypt
    kms_region: str
    kms_proxy_port: int


class RiskSettings(Strict):
    """settings/risk.yaml: the autonomy policy, passed to the enclave (FR-06)."""

    confidence_high: float = 0.8
    confidence_low: float = 0.5
    risk: dict[DeviceType, Risk]  # types not listed, and `other`, are high (wiki: Hub messages)


M = TypeVar("M", bound=BaseModel)


def load(model: type[M], name: str, directory: Path = SETTINGS_DIR) -> M:
    with open(directory / name) as f:
        return model.model_validate(yaml.safe_load(f))


def risk_of(settings: RiskSettings, device_type: DeviceType) -> Risk:
    if device_type == DeviceType.OTHER:
        return Risk.HIGH
    return settings.risk.get(device_type, Risk.HIGH)
