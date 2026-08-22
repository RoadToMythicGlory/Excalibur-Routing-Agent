from __future__ import annotations

import logging
import time

from excalibur.cv.frame_loader import load_pixels
from excalibur.cv.inference.engine import (
    InferenceEngine,
    ModelManifest,
    vision_routing_allowed,
)
from excalibur.cv.inference.mock_runner import MockInferenceEngine
from excalibur.cv.profiles import InferenceProfile
from excalibur.cv.tasks.camera_role import infer_camera_role
from excalibur.cv.tasks.event_detector import TemporalEventBuffer, infer_event_observation
from excalibur.matrix.clock import now_ms
from excalibur.matrix.frame_tap import FrameTap
from excalibur.matrix.latency import LatencyBudget, LatencyTracker
from excalibur.matrix.soccer.config import SoccerProfile
from excalibur.matrix.soccer.fusion import SoccerFusion

logger = logging.getLogger(__name__)


class SoccerVisionAgent:
    """Consumes frame taps, updates camera roles, and offers vision event fallback."""

    def __init__(
        self,
        fusion: SoccerFusion,
        profile: SoccerProfile,
        runtime: InferenceProfile,
        engine: InferenceEngine | None = None,
        manifest: ModelManifest | None = None,
        latency: LatencyTracker | None = None,
        budget: LatencyBudget | None = None,
    ) -> None:
        self.fusion = fusion
        self.profile = profile
        self.runtime = runtime
        self.engine = engine or MockInferenceEngine()
        self.manifest = manifest or getattr(self.engine, "manifest", None)
        self.latency = latency
        self.budget = budget or LatencyBudget()
        self.buffer = TemporalEventBuffer()
        self._stagger_index = 0
        self._healthy_ticks = 0
        self.degraded = False
        self.last_error: str | None = None
        self.routing_allowed = vision_routing_allowed(profile, self.manifest)
        # In shadow mode camera roles and freshness still flow into fusion,
        # but vision may never win routing authority.
        self.fusion.vision_authority_allowed = self.routing_allowed
        self.last_shadow_observation = None

    def process(self, frames: list[FrameTap]) -> None:
        if not frames:
            return
        start = time.perf_counter()
        ordered = frames
        if self.runtime.stagger and frames:
            ordered = [frames[self._stagger_index % len(frames)]]
            self._stagger_index += 1
        batch = ordered[: self.runtime.batch]
        for frame in batch:
            self._process_one(frame)
        elapsed = (time.perf_counter() - start) * 1000.0
        if self.latency is not None:
            self.latency.record(
                "end_to_end_vision", elapsed, self.budget.end_to_end_vision_ms
            )
        if elapsed > self.budget.end_to_end_vision_ms:
            self.degraded = True
            self.last_error = "vision_overload"
            self._healthy_ticks = 0
        elif self.degraded:
            # Recover from a transient stall after a run of healthy ticks so a
            # single slow frame does not flag the agent degraded forever.
            self._healthy_ticks += 1
            if self._healthy_ticks >= 5:
                self.degraded = False
                self.last_error = None
                self._healthy_ticks = 0

    def _process_one(self, frame: FrameTap) -> None:
        tap_start = now_ms()
        image = load_pixels(frame, width=self.runtime.width)
        if image is None:
            return
        if self.latency is not None:
            self.latency.record(
                "frame_tap_to_agent",
                max(0.0, tap_start - frame.captured_at),
                self.budget.frame_tap_to_agent_ms,
            )
        try:
            result = self.engine.infer(image)
        except Exception as error:  # pragma: no cover - defensive
            self.degraded = True
            self.last_error = str(error)
            logger.exception("soccer vision inference failed")
            return
        assignment = infer_camera_role(
            frame.input_id,
            image,
            result,
            self.profile.gating.min_role_confidence,
        )
        assignment.latency_ms = result.latency_ms
        self.fusion.ingest_camera_assignment(assignment)
        averaged = self.buffer.update(result)
        observation = infer_event_observation(
            averaged,
            self.profile.gating.min_event_confidence,
            input_id=frame.input_id,
        )
        if not self.routing_allowed:
            # Shadow mode: keep the observation visible for evaluation but
            # never feed it into event state.
            self.last_shadow_observation = observation
            return
        self.fusion.ingest_vision(observation)
