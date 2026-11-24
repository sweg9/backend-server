"""C-07: the explanation is a fixed template filled with the run's values."""

from datetime import UTC, datetime

from backend.common.vocab import Channel, DeviceAction, RunResult, Trigger
from backend.hub.explain import explain

AT = datetime(2026, 11, 24, 14, 30, tzinfo=UTC)  # 23:30 in Seoul


def test_success_names_the_channel_and_the_routine():
    s = explain(
        device_name="Living room light",
        action=DeviceAction.OFF,
        trigger=Trigger.TIME,
        started_at=AT,
        time_zone="Asia/Seoul",
        result=RunResult.SUCCESS,
        channel=Channel.ARM,
        reason=None,
        routine_time="23:30",
    )
    assert s == (
        "I switched off the living room light at 23:30 by pressing its switch because of your 23:30 routine. "
        "Checked: it worked."
    )


def test_cancelled_says_nothing_changed_and_why():
    s = explain(
        device_name="TV",
        action=DeviceAction.OFF,
        trigger=Trigger.SENSOR,
        started_at=AT,
        time_zone="UTC",
        result=RunResult.CANCELLED,
        channel=Channel.WIFI,
        reason="a person was in the work area",
    )
    assert s == "I did not switch off the TV at 14:30: a person was in the work area. Nothing was changed."
