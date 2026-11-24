"""WebSocket client to the server (wiki: Hub messages).

- Connects to `server_url` with HUB_TOKEN and reconnects with a growing wait (1 s to 30 s).
- Sends `hub.status` on connect and every `heartbeat_s`.
- Handles `stop` before anything else: it is answered at once, never queued behind app calls (FR-10).
- Hands each `app.request` to app.py in its own task and sends back the `app.response`.
- `ask` sends a request toward the enclave and waits for its answer up to a time limit; no answer in
  time gives None, and the caller decides on the safe side (wiki: Hub messages > Answers that do not come).
- `event` pushes an `app.event`; while offline it is dropped, since the app reads the lists again after
  it reconnects.
"""

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from backend.common.messages import (
    ANSWERS,
    AppEvent,
    AppRequest,
    AppResponse,
    Envelope,
    HubStatus,
    Msg,
    StopDone,
)

log = logging.getLogger(__name__)

AppHandler = Callable[[AppRequest], Awaitable[AppResponse]]
StopHandler = Callable[[], Awaitable[StopDone]]
StatusSource = Callable[[], HubStatus]


def envelope(type_: str, payload: Msg, re: str | None = None, run_id: str | None = None) -> Envelope:
    return Envelope(
        type=type_,
        id=f"m-h-{secrets.token_hex(6)}",
        re=re,
        run_id=run_id,
        sent_at=datetime.now(UTC),
        payload=payload.model_dump(mode="json"),
    )


class HubLink:
    def __init__(
        self,
        url: str,
        token: str,
        on_app: AppHandler,
        on_stop: StopHandler,
        status: StatusSource,
        heartbeat_s: float,
        max_wait_s: float = 30.0,
    ):
        self.url = url
        self.token = token
        self.on_app = on_app
        self.on_stop = on_stop
        self.status = status
        self.heartbeat_s = heartbeat_s
        self.max_wait_s = max_wait_s
        self._ws: ClientConnection | None = None
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[Envelope]] = {}
        self._tasks: set[asyncio.Task] = set()
        self.connected = asyncio.Event()

    # --- sending -------------------------------------------------------------------------------

    async def _send(self, msg: Envelope) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            async with self._send_lock:
                await ws.send(msg.model_dump_json())
            return True
        except websockets.ConnectionClosed:
            return False

    async def event(self, kind: str, data: dict[str, Any]) -> None:
        await self._send(envelope("app.event", AppEvent(kind=kind, data=data)))

    async def send_status(self) -> None:
        await self._send(envelope("hub.status", self.status()))

    async def ask(self, type_: str, payload: Msg, timeout_s: float, run_id: str | None = None) -> Envelope | None:
        msg = envelope(type_, payload, run_id=run_id)
        fut: asyncio.Future[Envelope] = asyncio.get_running_loop().create_future()
        self._pending[msg.id] = fut
        try:
            if not await self._send(msg):
                return None
            return await asyncio.wait_for(fut, timeout_s)
        except TimeoutError:
            log.warning("no %s for %s within %s s", ANSWERS[type_], msg.id, timeout_s)
            return None
        finally:
            self._pending.pop(msg.id, None)

    # --- receiving -----------------------------------------------------------------------------

    async def handle(self, msg: Envelope) -> None:
        """One message from the server."""
        if msg.type == "stop":
            done = await self.on_stop()
            await self._send(envelope("stop.done", done, re=msg.id))
        elif msg.type == "app.request":
            task = asyncio.create_task(self._answer_app(msg))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif msg.re is not None:
            fut = self._pending.get(msg.re)
            if fut is not None and not fut.done():
                fut.set_result(msg)
        else:
            log.warning("unexpected %s from the server", msg.type)

    async def _answer_app(self, msg: Envelope) -> None:
        try:
            resp = await self.on_app(AppRequest.model_validate(msg.payload))
        except Exception:
            log.exception("app.request %s failed", msg.id)
            resp = AppResponse(status=500, body={"code": "hub_error", "message": "The hub failed on this request"})
        await self._send(envelope("app.response", resp, re=msg.id))

    # --- the connection ------------------------------------------------------------------------

    async def _heartbeat(self) -> None:
        while True:
            await self.send_status()
            await asyncio.sleep(self.heartbeat_s)

    async def _session(self, ws: ClientConnection) -> None:
        self._ws = ws
        self.connected.set()
        beat = asyncio.create_task(self._heartbeat())
        try:
            async for raw in ws:
                try:
                    msg = Envelope.model_validate_json(raw)
                except ValueError:
                    log.warning("a message from the server did not match the contract")
                    continue
                await self.handle(msg)
        finally:
            beat.cancel()
            self._ws = None
            self.connected.clear()
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()

    async def run_forever(self) -> None:
        wait = 1.0
        headers = {"Authorization": f"Bearer {self.token}"}
        while True:
            try:
                async with websockets.connect(self.url, additional_headers=headers) as ws:
                    log.info("connected to the server at %s", self.url)
                    wait = 1.0
                    await self._session(ws)
                log.warning("the server closed the connection")
            except (OSError, websockets.InvalidHandshake, websockets.ConnectionClosed) as e:
                log.warning("cannot reach the server (%s); trying again in %.0f s", e, wait)
            await asyncio.sleep(wait)
            wait = min(wait * 2, self.max_wait_s)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
