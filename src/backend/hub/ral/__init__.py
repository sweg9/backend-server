"""Pick the RAL implementation from settings (C-13)."""

from collections.abc import Iterable

from backend.common.vocab import HandOption
from backend.hub.ral.base import RobotAbstractionLayer
from backend.hub.ral.script import ScriptRal


def make_ral(hand: HandOption, serial_port: str | None = None, actuators: Iterable[str] = ()) -> RobotAbstractionLayer:
    if hand == HandOption.SCRIPT:
        return ScriptRal()
    if hand == HandOption.LINEAR_ACTUATORS:
        from backend.hub.ral.linear_actuators import LinearActuatorsRal

        return LinearActuatorsRal(serial_port, actuators)
    raise NotImplementedError(f"RAL {hand} is not built yet")  # unity_sim.py
