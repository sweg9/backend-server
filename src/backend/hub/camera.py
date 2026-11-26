"""The Reader: takes one photo from the robot's camera when a step needs one, at most `photo_max_side` px
on the longest side, and seals it at once (FR-01, FR-08, C-05). No video is kept or sent, and no photo is
written to disk: the frame lives only in memory until it is sealed.

`photo()` gives None when the camera cannot give a frame or the photo cannot be sealed (no `kms_key_id`,
KMS refused): the step that needed it then goes on the safe side, as for an answer that does not come.

Exposure (FR-08): the camera check compares brightness before and after a command, and auto exposure
undoes most of that change within a second (seen on a test video: +25% right after a light went on,
+10% a second later). So `photo(lock=True)`, taken just before a press or a wireless command, lets auto
exposure settle on the room as it is, then locks it; the photos after the command keep that exposure.
OpenCV's exposure values depend on the camera API (`camera_api` in settings/hub.yaml): known for DirectShow
(the hub on Windows) and V4L2 (Linux); a Mac's AVFoundation has none, so there the photos go unlocked unless
both values are set. A camera that does not take the lock is logged once and used as it is.
"""

import asyncio
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from backend.common.ids import new_id
from backend.common.messages import Sealed
from backend.common.sealing import SealError
from backend.hub.seal import DataKeys, seal

log = logging.getLogger(__name__)


class Capture(Protocol):
    """The part of cv2.VideoCapture the Reader uses."""

    def isOpened(self) -> bool: ...  # noqa: N802 (OpenCV's name)

    def read(self) -> tuple[bool, Any]: ...

    def release(self) -> None: ...

    def get(self, prop: int) -> float: ...

    def set(self, prop: int, value: float) -> bool: ...


@dataclass(frozen=True)
class Exposure:
    """From settings/hub.yaml. `auto` and `manual` are the values of CAP_PROP_AUTO_EXPOSURE that mean auto
    and manual exposure for this camera's driver; `settle_frames` frames are read with auto exposure
    before it is locked."""

    lock: bool
    auto: float
    manual: float
    settle_frames: int


# CAP_PROP_AUTO_EXPOSURE values (auto, manual) per OpenCV camera API
EXPOSURE_VALUES: dict[str, tuple[float, float]] = {"dshow": (0.75, 0.25), "v4l2": (3, 1)}


def camera_api(name: str, platform: str = sys.platform) -> str:
    """`auto` becomes the API that works on this system: DirectShow on Windows, where OpenCV's default (Media
    Foundation) opens slowly and takes no exposure values."""
    if name != "auto":
        return name
    if platform.startswith("win"):
        return "dshow"
    return "avfoundation" if platform == "darwin" else "v4l2"


def exposure_for(api: str, lock: bool, auto: float | None, manual: float | None, settle_frames: int) -> Exposure | None:
    """The exposure lock for this API, or None (no lock) when its values are not known and not set."""
    if not lock:
        return None
    if auto is None or manual is None:
        known = EXPOSURE_VALUES.get(api)
        if known is None:
            log.warning(
                "no exposure values for camera_api %s: photos go unlocked unless camera_exposure_auto and "
                "camera_exposure_manual are set",
                api,
            )
            return None
        auto, manual = known
    return Exposure(True, auto, manual, settle_frames)


def opencv_opener(api: str) -> Callable[[int], Capture]:
    def open_capture(index: int) -> Capture:
        import cv2

        return cv2.VideoCapture(index, getattr(cv2, f"CAP_{api.upper()}") if api != "any" else cv2.CAP_ANY)

    return open_capture


_opencv = opencv_opener("any")


class Reader:
    """`keys()` gives the data keys for sealing (seal.data_keys), or raises SealError while photos must
    stay at home. `flush_frames` frames are read and dropped first, so the photo is from now and not
    from the camera's buffer."""

    def __init__(
        self,
        camera_index: int,
        max_side: int,
        jpeg_quality: int,
        flush_frames: int,
        keys: Callable[[], DataKeys],
        exposure: Exposure | None = None,
        open_capture: Callable[[int], Capture] = _opencv,
    ):
        self.camera_index = camera_index
        self.max_side = max_side
        self.jpeg_quality = jpeg_quality
        self.flush_frames = flush_frames
        self.keys = keys
        self.exposure = exposure
        self.open_capture = open_capture
        self._cap: Capture | None = None
        self._lock_failed_logged = False
        self._lock = asyncio.Lock()  # one photo at a time from one camera
        self.up = False  # for hub.status

    async def photo(self, lock: bool = False) -> Sealed | None:
        """One sealed photo. `lock`: settle and lock the exposure first (the photo before a command)."""
        async with self._lock:
            return await asyncio.to_thread(self._take, lock)

    def _take(self, lock: bool = False) -> Sealed | None:
        frame = self._frame(lock)
        if frame is None:
            return None
        try:
            jpeg, w, h = self._jpeg(frame)
            return seal(jpeg, photo_id=new_id("ph"), w=w, h=h, keys=self.keys())
        except SealError as e:
            log.warning("a photo was not sent: %s", e)  # SealError names the reason, never the photo
            return None

    def _frame(self, lock: bool = False) -> np.ndarray | None:
        if self._cap is None or not self._cap.isOpened():
            self._cap = self.open_capture(self.camera_index)
        ok, frame = False, None
        if self._cap.isOpened():
            if lock and self.exposure is not None and self.exposure.lock:
                self._lock_exposure(self._cap, self.exposure)
            for _ in range(self.flush_frames + 1):
                ok, frame = self._cap.read()
        if not ok or frame is None:
            log.warning("the camera gave no frame")
            self.close()
            self.up = False
            return None
        self.up = True
        return frame

    def _lock_exposure(self, cap: Capture, e: Exposure) -> None:
        import cv2

        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, e.auto)
        for _ in range(e.settle_frames):
            cap.read()
        value = cap.get(cv2.CAP_PROP_EXPOSURE)  # what auto exposure chose for the room as it is now
        ok = cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, e.manual) and cap.set(cv2.CAP_PROP_EXPOSURE, value)
        if not ok and not self._lock_failed_logged:
            log.warning("the camera did not take the exposure lock; camera checks may come out unsure more often")
            self._lock_failed_logged = True

    def _jpeg(self, frame: np.ndarray) -> tuple[bytes, int, int]:
        import cv2

        h, w = frame.shape[:2]
        scale = min(1.0, self.max_side / max(h, w))
        if scale < 1.0:
            w, h = max(1, round(w * scale)), max(1, round(h * scale))
            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            raise SealError("the photo could not be encoded as JPEG")
        return buf.tobytes(), w, h

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
