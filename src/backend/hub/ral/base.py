"""Robot Abstraction Layer (FR-11, C-13): the only way the hub moves the hand.

linear_actuators, unity_sim and script implement it, chosen by `hand` in settings/hub.yaml.
Nothing calls press() unless the Safety Guard passed right before it (C-04).
"""

from typing import Protocol

from backend.common.vocab import HandOption


class RobotAbstractionLayer(Protocol):
    impl: HandOption

    async def go_to_spot(self, spot_id: str) -> None:
        """In the prototype a team member pushes the cart; this waits for the presenter's confirmation."""

    async def press(self, actuator: str, push_ms: int) -> None: ...

    async def release(self, actuator: str) -> None: ...

    async def stop(self) -> None:
        """Turns off every actuator at once (FR-10). Must return quickly and never raise."""
