"""The serial RAL against a fake board that answers like firmware/actuator/actuator.ino (FR-10, FR-11, C-04, C-13)."""

import asyncio
import queue
import threading

import pytest

from backend.common.vocab import HandOption
from backend.hub.ral import make_ral
from backend.hub.ral.linear_actuators import HandError, LinearActuatorsRal


class FakeBoard:
    """Timed moves on threads; times in ms are divided by `scale` so the tests run fast."""

    def __init__(self, scale: float = 100.0, silent: bool = False):
        self.lines: list[str] = []
        self.out: queue.Queue[bytes] = queue.Queue()
        self.scale, self.silent = scale, silent
        self.stopped = False
        self.state = {"A1": "in", "A2": "in"}
        self.timers: dict[str, threading.Timer] = {}
        self.closed = False
        self.say("READY")

    def say(self, text: str) -> None:
        self.out.put((text + "\r\n").encode())

    def later(self, act: str, ms: float, then) -> None:
        t = threading.Timer(ms / 1000 / self.scale, then)
        self.timers[act] = t
        t.start()

    def write(self, data: bytes) -> None:
        line = data.decode().strip()
        self.lines.append(line)
        if self.silent:
            return
        cmd, *args = line.split()
        if cmd == "STOP":
            for a, t in list(self.timers.items()):
                t.cancel()
                if self.state[a] == "pushing":
                    self.say(f"ERR PRESS {a} stopped")
                if self.state[a] != "in":
                    self.state[a] = "held"
            self.timers.clear()
            self.stopped = True
            self.say("OK STOP")
        elif cmd == "RESET":
            self.stopped = False
            self.state = dict.fromkeys(self.state, "in")
            self.say("OK RESET")
        elif cmd == "PING":
            self.say("OK PING stopped" if self.stopped else "OK PING")
        elif cmd in ("PRESS", "RELEASE"):
            a = args[0]
            if self.stopped:
                self.say(f"ERR {cmd} {a} stopped")
            elif cmd == "PRESS":
                self.state[a] = "pushing"

                def done():
                    self.state[a] = "held"
                    self.say(f"OK PRESS {a}")

                self.later(a, int(args[1]), done)
            else:

                def back():
                    self.state[a] = "in"
                    self.say(f"OK RELEASE {a}")

                self.later(a, 100, back)
        else:
            self.say("ERR ? unknown_command")

    def readline(self) -> bytes:
        if self.closed:
            raise OSError("port closed")
        try:
            return self.out.get(timeout=0.02)
        except queue.Empty:
            return b""

    def close(self) -> None:
        self.closed = True


def ral_with(board: FakeBoard) -> LinearActuatorsRal:
    return LinearActuatorsRal("/dev/fake", ["A1", "A2"], opener=lambda port: board, boot_s=0)


@pytest.mark.fr("FR-11")
def test_press_then_release():
    board = FakeBoard()
    ral = ral_with(board)

    async def go():
        await ral.press("A1", 400)
        assert board.state["A1"] == "held"
        await ral.release("A1")
        await ral.close()

    asyncio.run(go())
    assert board.lines == ["PRESS A1 400", "RELEASE A1"]
    assert board.state["A1"] == "in"


@pytest.mark.fr("FR-10")
def test_stop_gets_through_while_a_press_waits():
    board = FakeBoard(scale=1)  # a real 2 s push
    ral = ral_with(board)

    async def go():
        press = asyncio.create_task(ral.press("A1", 2000))
        await asyncio.sleep(0.1)
        await ral.stop()
        with pytest.raises(HandError, match="stopped"):
            await press
        with pytest.raises(HandError, match="stopped"):  # C-04: no press after the stop until RESET
            await ral.press("A1", 400)
        await ral.close()

    asyncio.run(go())
    assert board.lines[-3:] == ["PRESS A1 2000", "STOP", "PRESS A1 400"]


@pytest.mark.fr("FR-10")
def test_reset_after_stop_is_sent_before_the_next_press():
    board = FakeBoard()
    ral = ral_with(board)

    async def go():
        await ral.press("A1", 400)
        await ral.stop()
        ral.reset()  # the orchestrator: the resident started something new
        await ral.press("A1", 400)
        await ral.release("A1")
        await ral.close()

    asyncio.run(go())
    assert board.lines == ["PRESS A1 400", "STOP", "RESET", "PRESS A1 400", "RELEASE A1"]


@pytest.mark.fr("FR-10")
def test_stop_never_raises():
    async def go():
        quiet = ral_with(FakeBoard(silent=True))
        await quiet.stop()  # never opened: nothing moved
        with pytest.raises(HandError, match="no answer"):
            await quiet.ping()
        await quiet.stop()  # opened, but no OK: logged, not raised
        await quiet.close()

    asyncio.run(go())


def test_wrong_calls_never_reach_the_board():
    board = FakeBoard()
    ral = ral_with(board)

    async def go():
        with pytest.raises(HandError, match="unknown actuator"):
            await ral.press("A9", 400)
        with pytest.raises(HandError, match="outside"):
            await ral.press("A1", 5000)
        assert await ral.ping() == ""
        await ral.close()

    asyncio.run(go())
    assert board.lines == ["PING"]


def test_board_that_goes_away_fails_the_waiting_call():
    board = FakeBoard(silent=True)
    ral = ral_with(board)

    async def go():
        task = asyncio.create_task(ral.press("A1", 400))
        await asyncio.sleep(0.1)
        board.closed = True
        with pytest.raises(HandError, match="went away"):
            await task
        await ral.close()

    asyncio.run(go())


def test_settings_pick_the_serial_ral():
    ral = make_ral(HandOption.LINEAR_ACTUATORS, "/dev/fake", ["A1"])
    assert ral.impl == HandOption.LINEAR_ACTUATORS
    with pytest.raises(ValueError, match="serial_port"):
        make_ral(HandOption.LINEAR_ACTUATORS, None, ["A1"])
