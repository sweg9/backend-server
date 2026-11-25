"""The program in the Nitro Enclave (wiki: Modules > enclave; Hub messages > The enclave link).

It listens on vsock (or on TCP for tests and development) and has no disk or network of its own.
The server's `init` brings risk.yaml, enclave.yaml, ai-engine's settings and the temporary AWS
credentials; the `Engine` is loaded on the first `init` and again only when those settings change.
Then each `ask` is answered by handlers.py, one at a time, and a failed request gets an `answer`
with no envelope. Sealed photos open only through KMS with this enclave's attestation (unseal.py),
so the same program run as a plain process over TCP opens none: there, only `decide` answers.
"""

import asyncio
import logging
import socket
from collections.abc import Callable

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
from backend.common.sealing import SealError
from backend.common.settings import EnclaveSettings, RiskSettings
from backend.enclave.handlers import Handlers

log = logging.getLogger(__name__)
CredentialsNow = Callable[[], Credentials | None]
Build = Callable[[Init, CredentialsNow], Handlers]


def build_handlers(engine_root: str | None) -> Build:
    """The real build: `ai_engine.Engine` with the model files under `engine_root`, and photos opened
    by `kmstool_enclave_cli` with the credentials the server sent last."""

    def build(init: Init, credentials: CredentialsNow) -> Handlers:
        from ai_engine import Engine

        from backend.enclave.unseal import KmstoolKeys, opener

        risk = RiskSettings.model_validate(init.risk)
        limits = EnclaveSettings.model_validate(init.enclave)

        def aws() -> tuple[str, str, str]:
            c = credentials()
            if c is None:
                raise SealError("no AWS credentials: not on AWS")
            return c.access_key_id, c.secret_access_key, c.session_token

        keys = KmstoolKeys(limits.kms_region, limits.kms_proxy_port, aws, limits.kmstool_timeout_s)
        return Handlers(Engine.load(init.engine, root=engine_root), init.engine, risk, opener(keys))

    return build


class EnclaveProgram:
    def __init__(self, build: Build):
        self.build = build
        self.handlers: Handlers | None = None
        self._loaded_with: tuple | None = None
        self._credentials: Credentials | None = None
        self._lock = asyncio.Lock()  # one model step at a time, whatever the connections

    async def connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                frame = await read_frame(reader)
                if isinstance(frame, Init):
                    await self._init(frame)
                    await write_frame(writer, Ready())
                elif isinstance(frame, NewCredentials):
                    self._credentials = frame.credentials
                elif isinstance(frame, Ask):
                    answer = await self._answer(frame.envelope)
                    await write_frame(writer, Answer(re=frame.envelope.id, envelope=answer))
                else:
                    raise LinkError(f"the server sent {frame.kind}")
        except LinkError as e:
            log.info("the server link closed: %s", e)
        except Exception as e:
            # A build that failed (a missing model file, bad settings): the server sees the link close
            # and tries again on its next request. Only the class, as the text may quote settings.
            log.error("the enclave could not start: %s", type(e).__name__)
        finally:
            writer.close()

    async def _init(self, init: Init) -> None:
        self._credentials = init.credentials
        key = (init.risk, init.enclave, init.engine)
        async with self._lock:
            if self.handlers is None or key != self._loaded_with:
                self.handlers = await asyncio.to_thread(self.build, init, lambda: self._credentials)
                self._loaded_with = key
                log.info("the engine is loaded")

    async def _answer(self, request: Envelope) -> Envelope | None:
        if self.handlers is None:
            return None
        async with self._lock:
            try:
                return await asyncio.to_thread(self.handlers.answer, request)
            except Exception as e:  # never let one request take the program down; the hub goes safe
                log.error("%s failed: %s", request.type, type(e).__name__, extra={"msg_id": request.id})
                return None


async def serve(program: EnclaveProgram, port: int, tcp_host: str | None = None) -> None:
    """vsock on `port` for any CID, or TCP on `tcp_host`:`port`."""
    if tcp_host is not None:
        server = await asyncio.start_server(program.connection, tcp_host, port)
    else:
        s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        s.bind((socket.VMADDR_CID_ANY, port))
        server = await asyncio.start_server(program.connection, sock=s)
    log.info("the enclave listens on %s port %d", tcp_host or "vsock", port)
    async with server:
        await server.serve_forever()
