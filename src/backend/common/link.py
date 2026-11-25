"""The link between the server and the enclave (wiki: Hub messages > The enclave link).

The server connects; the enclave listens, on vsock in the Nitro Enclave or on TCP for tests and
development. Each frame is a 4-byte big-endian length and then that many bytes of UTF-8 JSON, one
`Frame`. The server sends `init` first on every new connection; the enclave loads the `Engine` on
the first `init` only and answers `ready`. Then the server sends one `ask` at a time and the
enclave answers each with one `answer`, whose `envelope` is null when the request failed (the hub
then handles it on the safe side). `credentials` replaces the temporary AWS credentials before
they expire.
"""

import asyncio
import json
import struct
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from backend.common.messages import Envelope, Msg

MAX_FRAME_BYTES = 16 * 1024 * 1024  # a check carries several sealed photos of at most 1280 px
_LENGTH = struct.Struct(">I")


class LinkError(Exception):
    """The other side sent something that is not a frame, or closed the connection."""


class Credentials(Msg):
    """Temporary AWS credentials of the parent instance, for KMS Decrypt with attestation (C-05).
    Never logged and never written to disk (C-10)."""

    access_key_id: str
    secret_access_key: str = Field(repr=False)
    session_token: str = Field(repr=False)
    expires_at: datetime


class Init(Msg):
    kind: Literal["init"] = "init"
    risk: dict[str, Any]  # settings/risk.yaml
    enclave: dict[str, Any]  # settings/enclave.yaml
    engine: dict[str, Any]  # ai-engine's settings.yaml
    credentials: Credentials | None  # None outside AWS: sealed photos then do not open


class Ready(Msg):
    kind: Literal["ready"] = "ready"


class NewCredentials(Msg):
    kind: Literal["credentials"] = "credentials"
    credentials: Credentials


class Ask(Msg):
    kind: Literal["ask"] = "ask"
    envelope: Envelope


class Answer(Msg):
    kind: Literal["answer"] = "answer"
    re: str  # the id of the asked envelope
    envelope: Envelope | None  # None: no answer (wiki: Hub messages > Answers that do not come)


Frame = Annotated[Init | Ready | NewCredentials | Ask | Answer, Field(discriminator="kind")]
_FRAME = TypeAdapter(Frame)


def encode(frame: Msg) -> bytes:
    body = frame.model_dump_json().encode()
    if len(body) > MAX_FRAME_BYTES:
        raise LinkError(f"a frame of {len(body)} bytes is over {MAX_FRAME_BYTES}")
    return _LENGTH.pack(len(body)) + body


async def read_frame(reader: asyncio.StreamReader) -> Init | Ready | NewCredentials | Ask | Answer:
    try:
        (n,) = _LENGTH.unpack(await reader.readexactly(_LENGTH.size))
        if n > MAX_FRAME_BYTES:
            raise LinkError(f"a frame of {n} bytes is over {MAX_FRAME_BYTES}")
        body = await reader.readexactly(n)
    except asyncio.IncompleteReadError as e:
        raise LinkError("the connection closed") from e
    try:
        return _FRAME.validate_python(json.loads(body))
    except ValueError:  # bad JSON or not a frame; the error text may quote a payload, so it is dropped
        raise LinkError("not a frame") from None


async def write_frame(writer: asyncio.StreamWriter, frame: Msg) -> None:
    writer.write(encode(frame))
    await writer.drain()
