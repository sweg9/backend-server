"""Names with the exact spelling of skills/standards/data-model.md (C-02).

Change them only when data-model.md changes. A test checks them against that file.
"""

from enum import StrEnum


class Stage(StrEnum):
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"
    S5 = "S5"
    S6 = "S6"
    S7 = "S7"


class ControlKind(StrEnum):
    SWITCH = "switch"
    TOGGLE = "toggle"
    POWER_STRIP = "power_strip"


class ControlState(StrEnum):
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


class ControlAction(StrEnum):
    PRESS = "press"
    FLIP = "flip"
    TURN = "turn"


class MappingStatus(StrEnum):
    PENDING = "Pending"
    VERIFIED = "Verified"
    REJECTED = "Rejected"
    STALE = "Stale"


class Channel(StrEnum):
    IR = "IR"
    WIFI = "WIFI"
    BT = "BT"
    ARM = "ARM"


class DeviceType(StrEnum):
    LIGHT = "light"
    FAN = "fan"
    TV = "tv"
    AIR_PURIFIER = "air_purifier"
    HEATER = "heater"
    ELECTRIC_BLANKET = "electric_blanket"
    MICROWAVE = "microwave"
    OTHER = "other"


class Risk(StrEnum):
    LOW = "low"
    HIGH = "high"


class Independence(StrEnum):
    ACT_AND_TELL = "act_and_tell"
    ASK_FIRST = "ask_first"
    SUGGEST_ONLY = "suggest_only"


class RoutineSource(StrEnum):
    HABIT = "habit"
    MANUAL = "manual"


class RoutineFeedback(StrEnum):
    KEEP = "keep"
    CHANGE = "change"
    REMOVE = "remove"


class Trigger(StrEnum):
    SENSOR = "sensor"
    TIME = "time"
    ROUTINE = "routine"


class CheckAnswer(StrEnum):
    YES = "yes"
    NO = "no"
    UNSURE = "unsure"


class RunResult(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    UNSURE = "unsure"
    CANCELLED = "cancelled"


class Decision(StrEnum):
    """Result of the autonomy policy (C-03). From execution-log.md."""

    ACT = "act"
    ASK = "ask"
    SUGGEST = "suggest"


class HandOption(StrEnum):
    LINEAR_ACTUATORS = "linear_actuators"
    UNITY_SIM = "unity_sim"
    SCRIPT = "script"


class DeviceAction(StrEnum):
    """What a routine or a run does to a device."""

    ON = "on"
    OFF = "off"
