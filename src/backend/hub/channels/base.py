"""Channel adapter (FR-03, FR-07, C-13): one small adapter per wireless channel.

Each adapter has two abilities: tell whether the device can be reached, and send a command.
The result of a command is decided by the State Verifier (FR-08), not here.
"""

from dataclasses import dataclass
from typing import Protocol

from backend.common.vocab import Channel, DeviceAction


@dataclass(frozen=True)
class SendResult:
    sent: bool
    reported_state: str | None  # what the platform reports (WIFI, BT); None for IR
    latency_ms: int
    reason: str | None = None


class ChannelAdapter(Protocol):
    channel: Channel

    async def reachable(self, device_id: str, address: dict | None) -> bool: ...

    async def send(self, device_id: str, address: dict | None, action: DeviceAction) -> SendResult: ...
