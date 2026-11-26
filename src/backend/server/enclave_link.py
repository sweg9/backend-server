"""The server's end of the enclave link (wiki: Modules > server; Hub messages > The enclave link).

Hands each request (`recognize`, `check`, `refind`, `people`, `decide`) to the enclave unopened and
returns its answer, or None when there is none. One connection, one request at a time: the enclave
runs one model step at a time anyway. A new connection starts with `init` (the settings and the
temporary AWS credentials), so an enclave that restarted gets them again. Any failure closes the
connection and raises `EnclaveUnavailable`; the hub's time limit then handles it on the safe side.

Transports: vsock to the Nitro Enclave on the same instance, or TCP to the same enclave program run
as a plain process (tests and development only: outside the enclave KMS opens no photo).

Not built yet: putting the sealed photos in S3 on the way (store.py).
"""

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from typing import Protocol

from backend.common.link import (
    Answer,
    Ask,
    Credentials,
    Init,
    LinkError,
    NewCredentials,
    Ready,
    read_frame,
    write_frame,
)
from backend.common.messages import Envelope

log = logging.getLogger(__name__)
Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]
Connect = Callable[[], Awaitable[Streams]]


class EnclaveUnavailable(Exception):
    pass


class CredentialSource(Protocol):
    async def get(self) -> Credentials | None: ...


def tcp(host: str, port: int) -> Connect:
    async def connect() -> Streams:
        return await asyncio.open_connection(host, port)

    return connect


def vsock(cid: int, port: int) -> Connect:
    async def connect() -> Streams:
        if not hasattr(socket, "AF_VSOCK"):
            raise EnclaveUnavailable("this system has no vsock; use enclave_transport: tcp")
        s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        # A blocking connect in a thread, which works on any event loop. The server runs on the standard asyncio
        # loop (cli.py): uvloop refuses a (cid, port) address and cannot stream a vsock socket.
        try:
            await asyncio.to_thread(s.connect, (cid, port))
        except BaseException:
            s.close()
            raise
        s.setblocking(False)
        return await asyncio.open_connection(sock=s)

    return connect


class EnclaveLink:
    """`settings()` gives the init frame's risk, enclave and engine settings (dicts as parsed from
    their YAML files); `credentials` the temporary AWS credentials, or None outside AWS."""

    def __init__(
        self,
        connect: Connect,
        settings: Callable[[], tuple[dict, dict, dict]],
        credentials: CredentialSource,
        timeout_s: float,
        init_timeout_s: float,
    ):
        self.connect = connect
        self.settings = settings
        self.credentials = credentials
        self.timeout_s = timeout_s
        self.init_timeout_s = init_timeout_s
        self._streams: Streams | None = None
        self._sent: Credentials | None = None
        self._lock = asyncio.Lock()

    async def ask(self, request: Envelope) -> Envelope | None:
        async with self._lock:
            try:
                if self._streams is None:
                    await asyncio.wait_for(self._open(), self.init_timeout_s)
                return await asyncio.wait_for(self._ask(request), self.timeout_s)
            except (TimeoutError, OSError, LinkError, EnclaveUnavailable) as e:
                await self.close()
                # The class only: the text may name the request (L-07).
                raise EnclaveUnavailable(f"no answer from the enclave: {type(e).__name__}") from e

    async def _open(self) -> None:
        try:
            risk, enclave, engine = self.settings()
        except OSError as e:
            raise EnclaveUnavailable(f"a settings file for the enclave is missing: {e.filename}") from e
        self._streams = await self.connect()
        creds = await self.credentials.get()
        reader, writer = self._streams
        await write_frame(writer, Init(risk=risk, enclave=enclave, engine=engine, credentials=creds))
        if not isinstance(await read_frame(reader), Ready):
            raise LinkError("the enclave did not answer init with ready")
        self._sent = creds
        log.info("the enclave link is open")

    async def _ask(self, request: Envelope) -> Envelope | None:
        assert self._streams is not None
        reader, writer = self._streams
        creds = await self.credentials.get()
        if creds is not None and creds != self._sent:
            await write_frame(writer, NewCredentials(credentials=creds))
            self._sent = creds
        await write_frame(writer, Ask(envelope=request))
        frame = await read_frame(reader)
        if not isinstance(frame, Answer) or frame.re != request.id:
            raise LinkError("the enclave answered something else")
        return frame.envelope

    async def close(self) -> None:
        if self._streams is not None:
            writer = self._streams[1]
            self._streams = None
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
