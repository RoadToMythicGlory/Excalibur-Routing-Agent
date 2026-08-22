from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from excalibur.cv.inference.engine import InferenceResult, ModelManifest


class OnnxInferenceEngine:
    """Optional ONNX Runtime backend. Missing models fall back to the mock runner."""

    def __init__(self, manifest: ModelManifest, provider: str) -> None:
        self.manifest = manifest
        self.provider = provider
        self._session = None
        path = Path(manifest.path) if manifest.path else None
        if path and path.exists():
            try:
                import onnxruntime as ort  # type: ignore

                available = ort.get_available_providers()
                providers = [provider] if provider in available else ["CPUExecutionProvider"]
                self._session = ort.InferenceSession(str(path), providers=providers)
            except Exception:
                self._session = None

    def infer(self, image: np.ndarray) -> InferenceResult:
        start = time.perf_counter()
        if self._session is None:
            return InferenceResult(labels={}, latency_ms=0.0, extras={"backend": "onnx_missing"})
        tensor = self._preprocess(image)
        inputs = {self._session.get_inputs()[0].name: tensor}
        outputs = self._session.run(None, inputs)
        scores = np.asarray(outputs[0]).reshape(-1)
        labels = {
            self.manifest.classes[index]: float(score)
            for index, score in enumerate(scores)
            if index < len(self.manifest.classes)
        }
        elapsed = (time.perf_counter() - start) * 1000.0
        return InferenceResult(labels=labels, latency_ms=elapsed, extras={"backend": "onnx"})

    @staticmethod
    def _preprocess(image: np.ndarray) -> np.ndarray:
        array = image.astype(np.float32) / 255.0
        if array.ndim == 2:
            array = np.repeat(array[..., None], 3, axis=2)
        chw = np.transpose(array, (2, 0, 1))
        return np.expand_dims(chw, 0)
