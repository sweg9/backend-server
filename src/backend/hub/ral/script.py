"""RAL that only records the calls: for tests and runs without hardware."""

from backend.common.vocab import HandOption


class ScriptRal:
    impl = HandOption.SCRIPT

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.stopped = False

    async def go_to_spot(self, spot_id: str) -> None:
        self.calls.append(("go_to_spot", spot_id))

    async def press(self, actuator: str, push_ms: int) -> None:
        if self.stopped:
            raise RuntimeError("the hand is stopped")
        self.calls.append(("press", actuator, push_ms))

    async def release(self, actuator: str) -> None:
        self.calls.append(("release", actuator))

    async def stop(self) -> None:
        self.stopped = True
        self.calls.append(("stop",))
