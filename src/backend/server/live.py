"""WebSocket to the app: pushes every `app.event` from the hub (wiki: Hub messages > app.event).

The server keeps nothing; a screen that missed an event reads the lists again after a reconnect.
"""

import logging

from fastapi import WebSocket

from backend.common.messages import Envelope

log = logging.getLogger(__name__)


class Live:
    def __init__(self) -> None:
        self.screens: set[WebSocket] = set()

    async def push(self, event: Envelope) -> None:
        for ws in list(self.screens):
            try:
                await ws.send_json(event.payload)
            except Exception:
                log.info("an app screen went away")
                self.screens.discard(ws)
