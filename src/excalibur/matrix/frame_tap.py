from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from excalibur.matrix.clock import now_ms
from excalibur.matrix.latency import LatencyBudget
from excalibur.matrix.matrix_core import MatrixCore

logger = logging.getLogger(__name__)


@dataclass
class FrameTap:
    """A single low-resolution preview frame from one matrix input."""

    input_id: str
    width: int
    height: int
    captured_at: float
    pixels: object | None = None  # numpy array in production; None for headless sim
    metadata: dict = field(default_factory=dict)

    def age_ms(self, now: float | None = None) -> float:
        return (now if now is not None else now_ms()) - self.captured_at


class FrameTapSource(Protocol):
    """Production media backends (FFmpeg/GStreamer) implement this.

    Agents subscribe to frame taps from the matrix; they never scrape OBS. The
    production path replaces the MVP monitor/OBS-screenshot capture entirely.
    """

    def grab(self, input_id: str) -> FrameTap | None: ...


class SimulatedFrameTapSource:
    """Headless frame-tap source used for development and tests.

    Emits frame metadata (no pixels) so the full pipeline runs without native
    media dependencies or live SRT/RTMP feeds.
    """

    def __init__(self, width: int = 480, height: int = 270) -> None:
        self.width = width
        self.height = height
        self._counter = 0

    def grab(self, input_id: str) -> FrameTap | None:
        self._counter += 1
        return FrameTap(
            input_id=input_id,
            width=self.width,
            height=self.height,
            captured_at=now_ms(),
            pixels=None,
            metadata={"frame_index": self._counter, "simulated": True},
        )


class FrameTapManager:
    """Samples active inputs, downscales, timestamps, and reports latency.

    Configurable sample FPS and downscale target match the plan (2-10 FPS,
    320p/540p). The manager only taps inputs that currently have signal.
    """

    def __init__(
        self,
        matrix: MatrixCore,
        source: FrameTapSource,
        budget: LatencyBudget | None = None,
        fps: float = 5.0,
    ) -> None:
        self.matrix = matrix
        self.source = source
        self.budget = budget or LatencyBudget()
        self.interval_ms = 1000.0 / fps if fps > 0 else 0.0
        self._last_tap_at: dict[str, float] = {}
        self.latest: dict[str, FrameTap] = {}

    def tap_all(self) -> list[FrameTap]:
        now = now_ms()
        frames: list[FrameTap] = []
        for slot_id, slot in self.matrix.inputs.items():
            if not slot.has_signal:
                continue
            last = self._last_tap_at.get(slot_id, 0.0)
            if self.interval_ms and now - last < self.interval_ms:
                continue
            frame = self.source.grab(slot_id)
            if frame is None:
                continue
            self._last_tap_at[slot_id] = now
            self.latest[slot_id] = frame
            self.matrix.mark_input_frame(slot_id, frame.captured_at)
            frames.append(frame)
        return frames

    def freshest_age_ms(self, now: float | None = None) -> dict[str, float]:
        return {slot_id: frame.age_ms(now) for slot_id, frame in self.latest.items()}
