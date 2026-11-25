"""The Reader: takes one photo from the robot's camera when a step needs one, at most `photo_max_side` px
on the longest side, and seals it at once (FR-01, FR-08, C-05). No video is kept or sent, and no photo is
written to disk: the frame lives only in memory until it is sealed.

`photo()` gives None when the camera cannot give a frame or the photo cannot be sealed (no `kms_key_id`,
KMS refused): the step that needed it then goes on the safe side, as for an answer that does not come.
"""

import asyncio
import logging
from collections.abc import Callable
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


def _opencv(index: int) -> Capture:
    import cv2

    return cv2.VideoCapture(index)


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
        open_capture: Callable[[int], Capture] = _opencv,
    ):
        self.camera_index = camera_index
        self.max_side = max_side
        self.jpeg_quality = jpeg_quality
        self.flush_frames = flush_frames
        self.keys = keys
        self.open_capture = open_capture
        self._cap: Capture | None = None
        self._lock = asyncio.Lock()  # one photo at a time from one camera
        self.up = False  # for hub.status

    async def photo(self) -> Sealed | None:
        async with self._lock:
            return await asyncio.to_thread(self._take)

    def _take(self) -> Sealed | None:
        frame = self._frame()
        if frame is None:
            return None
        try:
            jpeg, w, h = self._jpeg(frame)
            return seal(jpeg, photo_id=new_id("ph"), w=w, h=h, keys=self.keys())
        except SealError as e:
            log.warning("a photo was not sent: %s", e)  # SealError names the reason, never the photo
            return None

    def _frame(self) -> np.ndarray | None:
        if self._cap is None or not self._cap.isOpened():
            self._cap = self.open_capture(self.camera_index)
        ok, frame = False, None
        if self._cap.isOpened():
            for _ in range(self.flush_frames + 1):
                ok, frame = self._cap.read()
        if not ok or frame is None:
            log.warning("the camera gave no frame")
            self.close()
            self.up = False
            return None
        self.up = True
        return frame

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
