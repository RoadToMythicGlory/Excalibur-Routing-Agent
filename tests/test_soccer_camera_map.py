from __future__ import annotations

from excalibur.matrix.soccer.camera_map import SoccerCameraMap
from excalibur.matrix.soccer.config import load_soccer_profile
from excalibur.matrix.soccer.domain import CameraAssignment, CameraRole


def test_input_one_stays_pinned_main():
    cameras = SoccerCameraMap(load_soccer_profile())
    cameras.update(
        CameraAssignment(
            input_id="input_01",
            role=CameraRole.GOAL_LINE.value,
            confidence=0.99,
            source="vision",
        )
    )
    assert cameras.role_for("input_01").role == CameraRole.MAIN_WIDE.value
    cameras.update(
        CameraAssignment(
            input_id="input_04",
            role=CameraRole.BEHIND_GOAL_LEFT.value,
            confidence=0.8,
            source="vision",
        )
    )
    assert cameras.inputs_for_role(CameraRole.BEHIND_GOAL_LEFT.value) == ["input_04"]
