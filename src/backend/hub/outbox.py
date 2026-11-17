"""The hub's outbox (wiki: Hub messages > Delivery).

Every message that needs an ack is appended to outbox.jsonl before it is sent and removed
when the ack arrives. After a reconnect or a restart, what is left is sent again in order.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.common.messages import HUB_OUTBOX_TYPES, Envelope


class Outbox:
    def __init__(self, path: Path, max_age_days: int):
        self.path = path
        self.max_age = timedelta(days=max_age_days)
        self._items: list[Envelope] = []
        if path.exists():
            self._items = [Envelope.model_validate_json(line) for line in path.read_text().splitlines() if line]

    def __len__(self) -> int:
        return len(self._items)

    def add(self, msg: Envelope) -> None:
        if msg.type not in HUB_OUTBOX_TYPES:
            raise ValueError(f"{msg.type} is not confirmed with ack and does not go in the outbox")
        self._items.append(msg)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(msg.model_dump_json() + "\n")

    def ack(self, ids: list[str]) -> None:
        done = set(ids)
        self._items = [m for m in self._items if m.id not in done]
        self._rewrite()

    def pending(self, now: datetime | None = None) -> list[Envelope]:
        """Messages to send again, oldest first. Drops the ones the cloud would no longer recognize."""
        now = now or datetime.now(UTC)
        keep = [m for m in self._items if now - m.sent_at <= self.max_age]
        if len(keep) != len(self._items):
            self._items = keep
            self._rewrite()
        return list(self._items)

    def _rewrite(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(m.model_dump_json() + "\n" for m in self._items))
        tmp.replace(self.path)
