"""Settings files (C-12). Every number the code compares against lives in settings/*.yaml.

A missing or unknown key stops the start: the models forbid extra keys.
"""

from pathlib import Path
from typing import TypeVar

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
    cloud_url: str
    hand: HandOption
    serial_port: str | None
    camera_index: int
    data_dir: str
    spots: dict[str, str]  # spot_id -> room
    actuators: list[Actuator]
    heartbeat_s: float
    channel_timeout_s: float  # FR-07
    effect_wait_s: float  # FR-08
    unsure_recheck_s: float  # FR-08
    max_press_tries: int  # FR-11
    watch_interval_s: float  # FR-04 uses
    watch_quiet_s: float
    outbox_max_age_days: int


class CloudSettings(Strict):
    database_url: str
    crop_dir: str
    time_zone: str
    single_worker: bool
    confidence_high: float  # FR-06
    confidence_low: float
    duplicate_trigger_s: float  # FR-05
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
    hub_offline_after_missed: int
    ack_batch_ms: int
    scan_start_ack_s: float
    stop_ack_s: float
    session_days: int
    login_max_failures: int
    login_window_s: float
    hub_messages_keep_days: int
    uses_keep_days: int
    crops_unreferenced_keep_days: int


class RiskSettings(Strict):
    risk: dict[DeviceType, Risk]  # types not listed, and `other`, are high (wiki: Hub messages)


M = TypeVar("M", bound=BaseModel)


def load(model: type[M], name: str, directory: Path = SETTINGS_DIR) -> M:
    with open(directory / name) as f:
        return model.model_validate(yaml.safe_load(f))


def risk_of(settings: RiskSettings, device_type: DeviceType) -> Risk:
    if device_type == DeviceType.OTHER:
        return Risk.HIGH
    return settings.risk.get(device_type, Risk.HIGH)
