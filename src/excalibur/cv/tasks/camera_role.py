from __future__ import annotations

import numpy as np

from excalibur.cv.frame_loader import coverage_score, sharpness_score
from excalibur.cv.inference.engine import InferenceResult
from excalibur.matrix.soccer.domain import CameraAssignment, CameraRole


def infer_camera_role(
    input_id: str,
    image: np.ndarray,
    result: InferenceResult,
    min_confidence: float,
) -> CameraAssignment:
    best_role = CameraRole.UNKNOWN.value
    best_score = 0.0
    for key, value in result.labels.items():
        if key.startswith("role:") and value > best_score:
            best_role = key.split(":", 1)[1]
            best_score = value
    coverage = coverage_score(image)
    sharpness = sharpness_score(image)
    if best_score < min_confidence:
        if coverage > 0.45:
            best_role = CameraRole.TACTICAL_HIGH.value
            best_score = 0.56
        elif sharpness > 0.45 and coverage < 0.25:
            best_role = CameraRole.SIDELINE_TIGHT.value
            best_score = 0.56
        else:
            best_role = CameraRole.UNKNOWN.value
    return CameraAssignment(
        input_id=input_id,
        role=best_role,
        confidence=float(best_score if best_score else 0.3),
        source="vision",
        sharpness=sharpness,
        coverage=coverage,
        latency_ms=result.latency_ms,
    )
