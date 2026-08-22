from __future__ import annotations

import time

import numpy as np

from excalibur.cv.inference.engine import InferenceResult, ModelManifest
from excalibur.matrix.soccer.domain import CameraRole, SoccerEventType


class MockInferenceEngine:
    """Deterministic runner used in tests and when no ONNX model is installed."""

    def __init__(
        self,
        labels: dict[str, float] | None = None,
        role: str = CameraRole.UNKNOWN.value,
        event_type: str | None = None,
        latency_ms: float = 4.0,
    ) -> None:
        self.forced_labels = labels or {}
        self.role = role
        self.event_type = event_type
        self.forced_latency_ms = latency_ms
        self.manifest = ModelManifest(
            version="mock-0",
            path=None,
            classes=[item.value for item in SoccerEventType] + [item.value for item in CameraRole],
            validated=False,
        )

    def infer(self, image: np.ndarray) -> InferenceResult:
        start = time.perf_counter()
        labels = dict(self.forced_labels)
        if self.role:
            labels[f"role:{self.role}"] = labels.get(f"role:{self.role}", 0.7)
        if self.event_type:
            labels[f"event:{self.event_type}"] = labels.get(f"event:{self.event_type}", 0.7)
        _ = image
        elapsed = (time.perf_counter() - start) * 1000.0
        return InferenceResult(
            labels=labels,
            latency_ms=max(self.forced_latency_ms, elapsed),
            extras={"backend": "mock"},
        )
