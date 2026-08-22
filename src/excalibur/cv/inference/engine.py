from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from excalibur.matrix.soccer.config import SoccerEvaluationGates, SoccerProfile


@dataclass
class ModelManifest:
    version: str
    path: str | None
    classes: list[str]
    checksum: str | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    validated: bool = False

    def passes(self, gates: SoccerEvaluationGates) -> bool:
        precision = self.metrics.get("precision", 0.0)
        recall = self.metrics.get("recall", 0.0)
        false_events = self.metrics.get("false_events_per_match", 99.0)
        latency = self.metrics.get("inference_ms", 999.0)
        switch_rate = self.metrics.get("switch_rate_per_min", 99.0)
        return (
            self.validated
            and precision >= gates.min_precision
            and recall >= gates.min_recall
            and false_events <= gates.max_false_events_per_match
            and latency <= gates.max_inference_ms
            and switch_rate <= gates.max_switch_rate_per_min
        )


@dataclass
class InferenceResult:
    labels: dict[str, float]
    latency_ms: float
    extras: dict = field(default_factory=dict)


class InferenceEngine(Protocol):
    manifest: ModelManifest

    def infer(self, image: np.ndarray) -> InferenceResult: ...


def load_manifest(path: str | Path | None) -> ModelManifest | None:
    if not path:
        return None
    target = Path(path)
    if not target.exists():
        return None
    import json

    data = json.loads(target.read_text(encoding="utf-8"))
    return ModelManifest(
        version=str(data.get("version", "0")),
        path=data.get("path"),
        classes=list(data.get("classes", [])),
        checksum=data.get("checksum"),
        metrics=dict(data.get("metrics") or {}),
        validated=bool(data.get("validated", False)),
    )


def vision_routing_allowed(profile: SoccerProfile, manifest: ModelManifest | None) -> bool:
    if manifest is None:
        return False
    if profile.inference.shadow_until_validated and not manifest.passes(
        profile.evaluation_gates
    ):
        return False
    return True
