from __future__ import annotations

import numpy as np

from excalibur.cv.agent.vision_agent import SoccerVisionAgent
from excalibur.cv.inference.mock_runner import MockInferenceEngine
from excalibur.cv.inference.onnx_runner import OnnxInferenceEngine
from excalibur.cv.profiles import resolve_runtime_profile
from excalibur.cv.tasks.camera_role import infer_camera_role
from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import now_ms
from excalibur.matrix.frame_tap import FrameTap
from excalibur.matrix.soccer.config import load_soccer_profile
from excalibur.matrix.soccer.domain import CameraRole
from excalibur.matrix.soccer.fusion import SoccerFusion


def test_mock_and_missing_onnx_are_safe():
    engine = MockInferenceEngine(role=CameraRole.TACTICAL_HIGH.value)
    image = np.zeros((64, 64, 3), dtype=np.uint8)
    result = engine.infer(image)
    assert result.labels
    assignment = infer_camera_role("input_02", image, result, 0.5)
    assert assignment.role == CameraRole.TACTICAL_HIGH.value
    missing = OnnxInferenceEngine(engine.manifest, "CPUExecutionProvider")
    assert missing.infer(image).extras["backend"] == "onnx_missing"


def test_vision_agent_updates_roles_without_routing():
    profile = load_soccer_profile()
    fusion = SoccerFusion(MatrixBuses(), profile)
    runtime = resolve_runtime_profile(profile, "cpu")
    agent = SoccerVisionAgent(
        fusion,
        profile,
        runtime,
        engine=MockInferenceEngine(role=CameraRole.SIDELINE_TIGHT.value),
    )
    assert agent.routing_allowed is False
    image = np.zeros((120, 160, 3), dtype=np.uint8)
    frame = FrameTap(
        input_id="input_03",
        width=160,
        height=120,
        captured_at=now_ms(),
        pixels=image,
    )
    agent.process([frame])
    assert fusion.cameras["input_03"].role == CameraRole.SIDELINE_TIGHT.value
    # Shadow mode still marks the vision source alive and records shadow
    # observations, but is never allowed routing authority.
    assert fusion.vision_authority_allowed is False
    assert fusion.last_vision_at is not None
    assert agent.last_shadow_observation is not None
    assert fusion.event is None


def test_vision_degraded_latch_recovers():
    profile = load_soccer_profile()
    fusion = SoccerFusion(MatrixBuses(), profile)
    runtime = resolve_runtime_profile(profile, "cpu")
    agent = SoccerVisionAgent(fusion, profile, runtime, engine=MockInferenceEngine())
    agent.degraded = True
    agent.last_error = "vision_overload"
    frame = FrameTap(
        input_id="input_01", width=160, height=120, captured_at=now_ms()
    )
    for _ in range(5):
        agent.process([frame])
    assert agent.degraded is False
    assert agent.last_error is None


def test_eval_manifest_gate():
    from excalibur.cv.inference.engine import ModelManifest

    profile = load_soccer_profile()
    good = ModelManifest(
        version="1",
        path=None,
        classes=list(profile.events),
        validated=True,
        metrics={
            "precision": 0.9,
            "recall": 0.8,
            "false_events_per_match": 1,
            "inference_ms": 40,
            "switch_rate_per_min": 8,
        },
    )
    bad = ModelManifest(version="1", path=None, classes=[], validated=True, metrics={})
    assert good.passes(profile.evaluation_gates) is True
    assert bad.passes(profile.evaluation_gates) is False
