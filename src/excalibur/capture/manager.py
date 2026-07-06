from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np

from excalibur.config import Rect, SourceConfig


@dataclass(frozen=True)
class Frame:
    source: str
    image_bgr: np.ndarray
    captured_at: float


class CaptureManager:
    def __init__(self, sources: list[SourceConfig]) -> None:
        self.sources = sources
        self._last_capture: dict[str, float] = {source.name: 0.0 for source in sources}

    def frames(self) -> Iterator[list[Frame]]:
        import mss

        with mss.mss() as screen_capture:
            while True:
                batch: list[Frame] = []
                now = time.monotonic()

                for source in self.sources:
                    interval = 1.0 / source.fps
                    if now - self._last_capture[source.name] < interval:
                        continue

                    monitor = screen_capture.monitors[source.monitor]
                    raw = np.array(screen_capture.grab(monitor))
                    image_bgr = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
                    if source.crop is not None:
                        image_bgr = crop_region(image_bgr, source.crop)

                    batch.append(
                        Frame(
                            source=source.name,
                            image_bgr=image_bgr,
                            captured_at=time.time(),
                        )
                    )
                    self._last_capture[source.name] = now

                if batch:
                    yield batch
                else:
                    time.sleep(0.005)


def crop_region(image_bgr: np.ndarray, region: Rect) -> np.ndarray:
    x, y, width, height = region
    return image_bgr[y : y + height, x : x + width]
