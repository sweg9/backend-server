"""A channel adapter for tests and the demo, which has no wireless channel (wiki: live-demo plan)."""

from backend.common.vocab import Channel, DeviceAction
from backend.hub.channels.base import SendResult


class StubAdapter:
    def __init__(self, channel: Channel, reachable: bool = True, reported_state: str | None = None):
        if channel == Channel.ARM:
            raise ValueError("ARM is the hand, not a wireless channel")
        self.channel = channel
        self._reachable = reachable
        self._reported = reported_state
        self.sent: list[tuple[str, DeviceAction]] = []

    async def reachable(self, device_id: str, address: dict | None) -> bool:
        return self._reachable

    async def send(self, device_id: str, address: dict | None, action: DeviceAction) -> SendResult:
        self.sent.append((device_id, action))
        return SendResult(sent=self._reachable, reported_state=self._reported, latency_ms=0)
