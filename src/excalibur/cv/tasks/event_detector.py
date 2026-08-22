from __future__ import annotations

from collections import defaultdict, deque

from excalibur.cv.inference.engine import InferenceResult
from excalibur.matrix.soccer.domain import (
    SPECIAL_EVENT_TYPES,
    SoccerObservationKind,
    SoccerPhase,
    VisionObservation,
)


class TemporalEventBuffer:
    """Smooths per-frame event scores so ordinary passes do not become events."""

    def __init__(self, window: int = 6) -> None:
        self.window = window
        self._scores: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=window))

    def update(self, result: InferenceResult) -> dict[str, float]:
        current = {key: 0.0 for key in SPECIAL_EVENT_TYPES}
        current[SoccerObservationKind.ORDINARY_PASS.value] = 0.0
        current[SoccerObservationKind.OPEN_PLAY.value] = 0.0
        for key, value in result.labels.items():
            name = key.split(":", 1)[1] if key.startswith("event:") else key
            if name in current:
                current[name] = value
        for name, value in current.items():
            self._scores[name].append(value)
        return {
            name: sum(values) / max(1, len(values)) for name, values in self._scores.items()
        }


def infer_event_observation(
    averaged: dict[str, float],
    min_confidence: float,
    input_id: str | None = None,
) -> VisionObservation:
    pass_score = averaged.get(SoccerObservationKind.ORDINARY_PASS.value, 0.0)
    open_play = averaged.get(SoccerObservationKind.OPEN_PLAY.value, 0.0)
    ranked = sorted(
        ((name, score) for name, score in averaged.items() if name in SPECIAL_EVENT_TYPES),
        key=lambda item: item[1],
        reverse=True,
    )
    if not ranked or ranked[0][1] < min_confidence or pass_score >= ranked[0][1]:
        kind = (
            SoccerObservationKind.ORDINARY_PASS.value
            if pass_score >= open_play
            else SoccerObservationKind.OPEN_PLAY.value
        )
        return VisionObservation(
            kind=kind,
            confidence=max(pass_score, open_play),
            input_id=input_id,
            evidence=["ordinary_play_or_pass"],
        )
    event_type, score = ranked[0]
    phase = SoccerPhase.EXECUTION.value if score >= 0.8 else SoccerPhase.SETUP.value
    return VisionObservation(
        kind=SoccerObservationKind.EVENT.value,
        confidence=score,
        event_type=event_type,
        phase=phase,
        input_id=input_id,
        evidence=[f"temporal_{event_type}={score:.2f}"],
    )
