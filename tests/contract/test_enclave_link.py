"""The server and the enclave program over the enclave link, on TCP in one process (wiki: Hub messages >
The enclave link). The enclave builds its handlers on ai_engine's FakeEngine; photos open to a gray frame."""

import asyncio
import struct
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
import yaml
from ai_engine import PersonBox
from ai_engine.testing import FakeEngine

from backend.common.link import MAX_FRAME_BYTES, Ask, Credentials, LinkError, encode, read_frame
from backend.common.messages import Envelope
from backend.common.sealing import SealError
from backend.common.settings import SETTINGS_DIR, RiskSettings, ServerSettings, load
from backend.enclave.handlers import Handlers
from backend.enclave.main import EnclaveProgram
from backend.server.enclave_link import EnclaveLink, EnclaveUnavailable, tcp
from backend.server.main import enclave_link
from tests.conftest import sealed

RISK = yaml.safe_load((SETTINGS_DIR / "risk.yaml").read_text())
RISK_MODEL = RiskSettings.model_validate(RISK)
ENCLAVE = {"kmstool_timeout_s": 10, "kms_region": "ap-northeast-2", "kms_proxy_port": 8000}
ENGINE = {"paths": {"fingerprint_weights": None}, "refind": {"max_shift": 0.1}, "people": {"conf": 0.25}}

DECIDE = {
    "trigger": "time",
    "device": {"type": "light", "risk": "low"},
    "action": "off",
    "mapping": {"status": "Verified", "confidence": 0.9},
    "routine": None,
    "confidence": 0.9,
}


def env(type_, payload, id_="m-h-1"):
    return Envelope(type=type_, id=id_, run_id="r-1", sent_at=datetime.now(UTC), payload=payload)


def creds(minutes=60, key="AKIA1"):
    return Credentials(
        access_key_id=key,
        secret_access_key="s",
        session_token="t",
        expires_at=datetime.now(UTC) + timedelta(minutes=minutes),
    )


class Creds:
    def __init__(self, value=None):
        self.value = value

    async def get(self):
        return self.value


class Builds:
    """Counts the engine loads and remembers the credentials getter each build was given."""

    def __init__(self, engine=None, fail=False):
        self.engine = engine or FakeEngine()
        self.fail = fail
        self.count = 0
        self.credentials = None

    def __call__(self, init, credentials):
        self.count += 1
        if self.fail:
            raise FileNotFoundError("models/detector.pt")
        self.credentials = credentials

        def open_photo(s):
            if credentials() is None:
                raise SealError("no AWS credentials: not on AWS")
            return np.full((s.h, s.w, 3), 128, dtype=np.uint8)

        return Handlers(self.engine, init.engine, RISK_MODEL, open_photo)


async def start(program):
    """The enclave program on a free TCP port; returns (task, port)."""
    server = await asyncio.start_server(program.connection, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    task = asyncio.create_task(server.serve_forever())
    return server, task, port


def link(port, credentials=None, timeout_s=2):
    return EnclaveLink(tcp("127.0.0.1", port), lambda: (RISK, ENCLAVE, ENGINE), Creds(credentials), timeout_s, 5)


async def stop(server, task):
    server.close()
    task.cancel()


# ---------- frames


def test_a_frame_is_a_length_and_json():
    data = encode(Ask(envelope=env("decide", DECIDE)))
    (n,) = struct.unpack(">I", data[:4])
    assert n == len(data) - 4 and data[4:5] == b"{"


def test_a_frame_over_the_limit_is_refused_before_reading_it():
    async def go():
        r = asyncio.StreamReader()
        r.feed_data(struct.pack(">I", MAX_FRAME_BYTES + 1))
        with pytest.raises(LinkError, match="over"):
            await read_frame(r)

    asyncio.run(go())


def test_bytes_that_are_not_a_frame_are_refused():
    async def go():
        r = asyncio.StreamReader()
        r.feed_data(struct.pack(">I", 13) + b'{"kind":"no"}')
        with pytest.raises(LinkError, match="not a frame"):
            await read_frame(r)

    asyncio.run(go())


# ---------- the server and the enclave


@pytest.mark.fr("FR-06")
def test_decide_goes_over_the_link_and_the_engine_loads_once():
    async def go():
        builds = Builds()
        server, task, port = await start(EnclaveProgram(builds))
        lk = link(port)
        a = await lk.ask(env("decide", DECIDE, "m-h-1"))
        b = await lk.ask(env("decide", DECIDE, "m-h-2"))
        await stop(server, task)
        return a, b, builds.count

    a, b, loads = asyncio.run(go())
    assert a.type == "decide.result" and a.re == "m-h-1" and a.body().decision == "act"
    assert b.re == "m-h-2"
    assert loads == 1


@pytest.mark.fr("FR-10")
def test_a_photo_that_does_not_open_gives_no_answer():
    """Outside AWS there are no credentials, so no photo opens: people answers nothing, and the hub
    cancels the press (C-04, C-05)."""

    async def go():
        server, task, port = await start(EnclaveProgram(Builds()))
        a = await link(port, credentials=None).ask(env("people", {"photo": sealed(), "work_area": [0, 0, 0.5, 1]}))
        await stop(server, task)
        return a

    assert asyncio.run(go()) is None


@pytest.mark.fr("FR-10")
def test_the_credentials_reach_the_photo_opener_and_are_replaced():
    async def go():
        engine = FakeEngine()
        engine.script("find_people", [PersonBox(kind="person", box=(0.1, 0.1, 0.3, 0.9), confidence=0.8, reason="x")])
        builds = Builds(engine)
        server, task, port = await start(EnclaveProgram(builds))
        source = Creds(creds(key="AKIA1"))
        lk = EnclaveLink(tcp("127.0.0.1", port), lambda: (RISK, ENCLAVE, ENGINE), source, 2, 5)
        a = await lk.ask(env("people", {"photo": sealed(), "work_area": [0, 0, 0.5, 1]}, "m-h-1"))
        first = builds.credentials().access_key_id
        source.value = creds(key="AKIA2")
        await lk.ask(env("decide", DECIDE, "m-h-2"))
        second = builds.credentials().access_key_id
        await stop(server, task)
        return a, first, second

    a, first, second = asyncio.run(go())
    assert a.type == "people.result" and a.body().clear is False
    assert (first, second) == ("AKIA1", "AKIA2")


def test_no_enclave_listening_raises_unavailable():
    async def go():
        server, task, port = await start(EnclaveProgram(Builds()))
        await stop(server, task)
        await asyncio.sleep(0.05)
        with pytest.raises(EnclaveUnavailable):
            await link(port).ask(env("decide", DECIDE))

    asyncio.run(go())


def test_an_enclave_that_restarted_gets_init_again():
    async def go():
        first = EnclaveProgram(Builds())
        writers = []

        async def connection(reader, writer):
            writers.append(writer)
            await first.connection(reader, writer)

        server = await asyncio.start_server(connection, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        task = asyncio.create_task(server.serve_forever())
        lk = link(port)
        await lk.ask(env("decide", DECIDE, "m-h-1"))
        # The enclave restarts on the same port: its end of the connection is gone.
        await stop(server, task)
        for w in writers:
            w.close()
        await asyncio.sleep(0.05)
        second = Builds()
        server = await asyncio.start_server(EnclaveProgram(second).connection, "127.0.0.1", port)
        task = asyncio.create_task(server.serve_forever())
        with pytest.raises(EnclaveUnavailable):
            await lk.ask(env("decide", DECIDE, "m-h-2"))  # found on the dead connection
        a = await lk.ask(env("decide", DECIDE, "m-h-3"))
        await stop(server, task)
        return a, second.count

    a, loads = asyncio.run(go())
    assert a.re == "m-h-3" and loads == 1


def test_an_engine_that_does_not_load_closes_the_link():
    async def go():
        server, task, port = await start(EnclaveProgram(Builds(fail=True)))
        with pytest.raises(EnclaveUnavailable):
            await link(port).ask(env("decide", DECIDE))
        await stop(server, task)

    asyncio.run(go())


def test_an_enclave_that_does_not_answer_in_time_raises_unavailable():
    async def go():
        engine = FakeEngine()
        program = EnclaveProgram(Builds(engine))
        real = program._answer

        async def slow(request):
            await asyncio.sleep(1)
            return await real(request)

        program._answer = slow
        server, task, port = await start(program)
        with pytest.raises(EnclaveUnavailable):
            await link(port, timeout_s=0.2).ask(env("decide", DECIDE))
        await stop(server, task)

    asyncio.run(go())


def test_the_server_settings_make_a_tcp_link(tmp_path):
    for name, text in {"risk.yaml": "risk: {}", "enclave.yaml": "a: 1", "ai-engine.yaml": "b: 2"}.items():
        (tmp_path / name).write_text(text)
    s = load(ServerSettings, "server.yaml").model_copy(
        update={"enclave_transport": "tcp", "enclave_host": "127.0.0.1", "aws_credentials": "none"}
    )
    lk = enclave_link(s, tmp_path)
    assert lk.settings() == ({"risk": {}}, {"a": 1}, {"b": 2})


def test_the_secret_parts_of_the_credentials_never_print():
    """C-10: a log line that prints the credentials by mistake shows neither the secret nor the token."""
    text = repr(creds()) + str(creds())
    assert "secret_access_key" not in text and "session_token" not in text
