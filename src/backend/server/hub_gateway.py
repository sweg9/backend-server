"""The hub's side of the server (wiki: Hub messages).

The server keeps no records. It hands the hub's requests to the Nitro Enclave unopened and sends the
answers back, relays the web app's calls to the hub (`app.request`) and the emergency `stop`, and
pushes `app.event` to the app. Requests that get no answer in time raise `HubOffline`, so the app
gets `503`.
"""

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

from backend.common.messages import (
    ANSWERS,
    ENCLAVE_REQUESTS,
    AppRequest,
    AppResponse,
    Envelope,
    HubStatus,
    Msg,
    Stop,
    StopDone,
)

log = logging.getLogger(__name__)
Send = Callable[[Envelope], Awaitable[None]]


class HubOffline(Exception):
    """The hub is not connected, or did not answer in time."""


class Enclave(Protocol):
    async def ask(self, request: Envelope) -> Envelope | None: ...


class Live(Protocol):
    async def push(self, event: Envelope) -> None: ...


def envelope(type_: str, payload: Msg, re: str | None = None, run_id: str | None = None) -> Envelope:
    return Envelope(
        type=type_,
        id=f"m-s-{secrets.token_hex(6)}",
        re=re,
        run_id=run_id,
        sent_at=datetime.now(UTC),
        payload=payload.model_dump(mode="json"),
    )


class HubGateway:
    def __init__(self, enclave: Enclave, live: Live, app_timeout_s: float, stop_timeout_s: float):
        self.enclave = enclave
        self.live = live
        self.app_timeout_s = app_timeout_s
        self.stop_timeout_s = stop_timeout_s
        self.status: HubStatus | None = None
        self._send: Send | None = None
        self._pending: dict[str, asyncio.Future[Envelope]] = {}

    @property
    def online(self) -> bool:
        return self._send is not None

    def attach(self, send: Send) -> None:
        self._send = send

    def detach(self) -> None:
        self._send = None
        self.status = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(HubOffline("the hub disconnected"))
        self._pending.clear()

    async def _ask_hub(self, type_: str, payload: Msg, timeout_s: float) -> Envelope:
        if self._send is None:
            raise HubOffline("the hub is not connected")
        msg = envelope(type_, payload)
        fut: asyncio.Future[Envelope] = asyncio.get_running_loop().create_future()
        self._pending[msg.id] = fut
        try:
            await self._send(msg)
            return await asyncio.wait_for(fut, timeout_s)
        except TimeoutError as e:
            raise HubOffline(f"no {ANSWERS[type_]} within {timeout_s} s") from e
        finally:
            self._pending.pop(msg.id, None)

    async def app_request(self, req: AppRequest) -> AppResponse:
        answer = await self._ask_hub("app.request", req, self.app_timeout_s)
        return AppResponse.model_validate(answer.payload)

    async def stop(self) -> StopDone:
        answer = await self._ask_hub("stop", Stop(source="app"), self.stop_timeout_s)
        return StopDone.model_validate(answer.payload)

    async def handle(self, msg: Envelope) -> Envelope | None:
        """One message from the hub. Returns the answer to send back, if there is one."""
        msg.body()  # checks the payload against its model
        if msg.type in ENCLAVE_REQUESTS:
            try:
                return await self.enclave.ask(msg)
            except Exception:
                # No answer: the hub's time limit handles it on the safe side (wiki: Hub messages).
                log.exception("the enclave did not answer %s %s", msg.type, msg.id)
                return None
        if msg.re is not None:
            fut = self._pending.get(msg.re)
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return None
        if msg.type == "app.event":
            await self.live.push(msg)
        elif msg.type == "hub.status":
            self.status = HubStatus.model_validate(msg.payload)
        return None
