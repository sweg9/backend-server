"""RAL for the linear actuators on the cart: an Arduino over USB serial (firmware/actuator/actuator.ino).

One command per line; the board answers `OK <CMD> [<actuator>]` or `ERR <CMD> [<actuator>] <reason>`
(firmware/README.md). A thread reads the lines and hands each answer to the call waiting for it, so `stop()`
gets through while a `press()` still waits for its answer (FR-10).

The board refuses PRESS after STOP until RESET. The orchestrator calls `reset()` when the resident starts
something new after the stop; the next press sends RESET first, which pulls every actuator back.
"""

import asyncio
import logging
import threading
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from backend.common.vocab import HandOption

log = logging.getLogger(__name__)

BAUD = 115200
BOOT_S = 2.0  # an Uno restarts when the port opens
MAX_PUSH_MS = 2000  # the board's cap (MAX_PUSH_MS in the sketch)
ANSWER_MARGIN_S = 2.0
STOP_WAIT_S = 1.0
RESET_WAIT_S = MAX_PUSH_MS * 1.2 / 1000 + ANSWER_MARGIN_S + 1.0


class HandError(RuntimeError):
    """The board said ERR, did not answer in time, or is not connected."""


class SerialLike(Protocol):
    def write(self, data: bytes) -> Any: ...

    def readline(self) -> bytes: ...

    def close(self) -> None: ...


def open_serial(port: str) -> SerialLike:
    import serial  # pyserial, in the hub extra

    return serial.Serial(port, BAUD, timeout=0.2)


class LinearActuatorsRal:
    impl = HandOption.LINEAR_ACTUATORS

    def __init__(
        self,
        port: str | None,
        actuators: Iterable[str],
        opener: Callable[[str], SerialLike] = open_serial,
        boot_s: float = BOOT_S,
    ) -> None:
        if not port:
            raise ValueError("hand: linear_actuators needs serial_port in settings/hub.yaml")
        self.port = port
        self.actuators = set(actuators)
        self._opener = opener
        self._boot_s = boot_s
        self._ser: SerialLike | None = None
        self._reader: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiting: dict[str, asyncio.Future] = {}
        self._write_lock = threading.Lock()
        self._connect_lock = asyncio.Lock()
        self._reset_due = False

    # --- the four calls of the RAL ---------------------------------------------------------------

    async def go_to_spot(self, spot_id: str) -> None:
        """The actuators are held or pushed by a team member in the prototype; the orchestrator shows the move."""
        log.info("hand: to spot %s (moved by a person)", spot_id)

    async def press(self, actuator: str, push_ms: int) -> None:
        self._check(actuator)
        if not 1 <= push_ms <= MAX_PUSH_MS:
            raise HandError(f"push_ms {push_ms} is outside 1 to {MAX_PUSH_MS}")
        await self._connect()
        if self._reset_due:
            await self.reset_now()
        await self._ask(f"PRESS {actuator} {push_ms}", f"PRESS {actuator}", push_ms / 1000 + ANSWER_MARGIN_S)

    async def release(self, actuator: str) -> None:
        self._check(actuator)
        await self._connect()
        await self._ask(f"RELEASE {actuator}", f"RELEASE {actuator}", MAX_PUSH_MS * 1.2 / 1000 + ANSWER_MARGIN_S)

    async def stop(self) -> None:
        """Never raises. A board that was never opened has moved nothing."""
        if self._ser is None:
            log.warning("hand: stop before the board was opened; nothing to stop")
            return
        try:
            await self._ask("STOP", "STOP", STOP_WAIT_S)
        except Exception:
            log.exception("hand: STOP got no OK; switch the actuators' power off by hand (C-04)")

    def reset(self) -> None:
        """The resident started something new after the stop: the next press sends RESET first."""
        self._reset_due = True

    async def reset_now(self) -> None:
        """RESET at once: every actuator goes back and PRESS is accepted again (a person's call, C-04)."""
        await self._connect()
        await self._ask("RESET", "RESET", RESET_WAIT_S)
        self._reset_due = False

    async def ping(self) -> str:
        await self._connect()
        return await self._ask("PING", "PING", ANSWER_MARGIN_S)

    async def close(self) -> None:
        ser, self._ser = self._ser, None
        if ser is not None:
            await asyncio.to_thread(ser.close)
        if self._reader is not None:
            await asyncio.to_thread(self._reader.join, 1.0)
            self._reader = None

    # --- the line protocol -----------------------------------------------------------------------

    def _check(self, actuator: str) -> None:
        if actuator not in self.actuators:
            raise HandError(f"unknown actuator {actuator}; hub.yaml has {sorted(self.actuators)}")

    async def _connect(self) -> None:
        async with self._connect_lock:
            if self._ser is not None:
                return
            self._loop = asyncio.get_running_loop()
            try:
                ser = await asyncio.to_thread(self._opener, self.port)
            except Exception as e:
                raise HandError(f"cannot open {self.port}: {e}") from e
            self._ser = ser
            self._reader = threading.Thread(target=self._read, args=(ser,), name="hand-serial", daemon=True)
            self._reader.start()
            await asyncio.sleep(self._boot_s)
            log.info("hand: board on %s", self.port)

    async def _ask(self, line: str, key: str, timeout_s: float) -> str:
        """Send one line and wait for the answer with this key: `OK <key>` returns the rest, `ERR <key>` raises."""
        ser = self._ser
        if ser is None:
            raise HandError("the board is not connected")
        if key in self._waiting:
            raise HandError(f"{key} is already waiting for an answer")
        fut = asyncio.get_running_loop().create_future()
        self._waiting[key] = fut
        try:
            await asyncio.to_thread(self._write, ser, line)
            return await asyncio.wait_for(fut, timeout_s)
        except TimeoutError:
            raise HandError(f"no answer to {line} in {timeout_s:.1f} s") from None
        finally:
            self._waiting.pop(key, None)

    def _write(self, ser: SerialLike, line: str) -> None:
        with self._write_lock:
            ser.write((line + "\n").encode("ascii"))

    def _read(self, ser: SerialLike) -> None:
        while self._ser is ser:
            try:
                raw = ser.readline()
            except Exception as e:
                if self._ser is ser:
                    log.error("hand: serial read failed: %s", e)
                    self._ser = None
                    self._deliver_all(HandError(f"the board went away: {e}"))
                return
            if raw:
                self._on_line(raw.decode("ascii", "replace").strip())

    def _on_line(self, text: str) -> None:
        words = text.split()
        if not words:
            return
        if words[0] not in ("OK", "ERR") or len(words) < 2:
            log.info("hand: board says %s", text)  # READY, EVT RELEASE A1 hold_timeout
            return
        cmd = words[1]
        takes_actuator = cmd in ("PRESS", "RELEASE")
        key = " ".join(words[1:3]) if takes_actuator else cmd
        rest = " ".join(words[3:] if takes_actuator else words[2:])
        if words[0] == "OK":
            self._deliver(key, rest)
        else:
            log.warning("hand: %s", text)
            self._deliver(key, HandError(f"board refused {key}: {rest or 'no reason'}"))

    def _deliver(self, key: str, result: str | Exception) -> None:
        loop = self._loop
        if loop is None:
            return

        def set_() -> None:
            fut = self._waiting.get(key)
            if fut is None or fut.done():
                log.info("hand: answer for %s with nobody waiting", key)
            elif isinstance(result, Exception):
                fut.set_exception(result)
            else:
                fut.set_result(result)

        loop.call_soon_threadsafe(set_)

    def _deliver_all(self, error: Exception) -> None:
        for key in list(self._waiting):
            self._deliver(key, error)
