from __future__ import annotations

from dataclasses import dataclass

from excalibur.matrix.soccer.config import SoccerProfile, SoccerRuntimeProfile


@dataclass(frozen=True)
class InferenceProfile:
    name: str
    width: int
    fps: float
    batch: int
    provider: str
    stagger: bool


def gpu_available() -> bool:
    try:
        import onnxruntime as ort  # type: ignore

        return "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


def resolve_runtime_profile(
    profile: SoccerProfile, requested: str | None = None
) -> InferenceProfile:
    choice = (requested or profile.inference.default_profile or "auto").lower()
    if choice == "auto":
        choice = "gpu" if gpu_available() else "cpu"
    raw: SoccerRuntimeProfile = profile.inference.gpu if choice == "gpu" else profile.inference.cpu
    if choice == "gpu" and not gpu_available():
        raw = profile.inference.cpu
        choice = "cpu"
    return InferenceProfile(
        name=choice,
        width=raw.width,
        fps=raw.fps,
        batch=raw.batch,
        provider=raw.provider,
        stagger=raw.stagger,
    )
