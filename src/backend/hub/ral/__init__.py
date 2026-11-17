"""Pick the RAL implementation from settings (C-13)."""

from backend.common.vocab import HandOption
from backend.hub.ral.base import RobotAbstractionLayer
from backend.hub.ral.script import ScriptRal


def make_ral(hand: HandOption) -> RobotAbstractionLayer:
    if hand == HandOption.SCRIPT:
        return ScriptRal()
    raise NotImplementedError(f"RAL {hand} is not built yet")  # linear_actuators.py, unity_sim.py
