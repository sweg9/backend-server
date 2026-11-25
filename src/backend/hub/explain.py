"""Fixed sentence templates for the plain-language explanation of a run (C-07).

No free text and no model output: every sentence is one of these templates filled with the run's own
values, so the resident always reads the same shape.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.common.vocab import Channel, DeviceAction, RunResult, Trigger

HOW = {
    Channel.ARM: "by pressing its switch",
    Channel.WIFI: "over Wi-Fi",
    Channel.BT: "over Bluetooth",
    Channel.IR: "with the IR remote",
}
CHECKED = {
    RunResult.SUCCESS: "it worked",
    RunResult.FAILURE: "it did not work",
    RunResult.UNSURE: "I could not tell if it worked",
}


def _why(trigger: Trigger, routine_time: str | None, retried: bool = False) -> str:
    if retried:
        return "you asked me to try again"
    if routine_time:
        return f"of your {routine_time} routine"
    return {
        Trigger.SENSOR: "a sensor triggered it",
        Trigger.TIME: "it was the set time",
        Trigger.ROUTINE: "of your routine",
    }[trigger]


def in_sentence(device_name: str) -> str:
    """'Living room light' reads 'the living room light'; names in capitals such as 'TV' stay."""
    if not device_name or device_name.isupper():
        return device_name
    return device_name[0].lower() + device_name[1:]


def explain(
    *,
    device_name: str,
    action: DeviceAction,
    trigger: Trigger,
    started_at: datetime,
    time_zone: str,
    result: RunResult,
    channel: Channel | None,
    reason: str | None,
    routine_time: str | None = None,
    retried: bool = False,
) -> str:
    at = started_at.astimezone(ZoneInfo(time_zone)).strftime("%H:%M")
    name = in_sentence(device_name)
    if result == RunResult.CANCELLED:
        return f"I did not switch {action} the {name} at {at}: {reason}. Nothing was changed."
    how = f" {HOW[channel]}" if channel else ""
    verb = f"switched {action}" if result == RunResult.SUCCESS else f"tried to switch {action}"
    checked = CHECKED[result]
    if result != RunResult.SUCCESS and reason:
        checked += f" ({reason})"
    return f"I {verb} the {name} at {at}{how} because {_why(trigger, routine_time, retried)}. Checked: {checked}."
