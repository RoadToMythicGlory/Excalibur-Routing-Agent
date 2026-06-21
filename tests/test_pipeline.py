"""End-to-end pipeline check without screen capture or a real OBS.

Synthetic frames are pushed through detection -> director -> OBS routing so the
full scene-switch path is exercised. The capture layer (mss) is the only piece
not covered here, since it requires real monitors.
"""

from pathlib import Path

import numpy as np

from obs_auto_director.capture.manager import Frame
from obs_auto_director.config import (
    DirectorConfig,
    EventConfig,
    GameProfile,
    ObsConfig,
    RegionColorRuleConfig,
)
from obs_auto_director.detection.rules import RuleEvaluator
from obs_auto_director.director.engine import DirectorEngine
from obs_auto_director.obs.client import ObsSceneRouter


class FakeReqClient:
    def __init__(self, scene_names: list[str]) -> None:
        self._scene_names = scene_names
        self.switched_to: list[str] = []

    def get_scene_list(self):
        return type("Resp", (), {"scenes": [{"sceneName": n} for n in self._scene_names]})()

    def set_current_program_scene(self, name: str) -> None:
        self.switched_to.append(name)


def make_red_hud_frame(source: str) -> Frame:
    image = np.zeros((40, 40, 3), dtype=np.uint8)
    image[0:40, 0:40] = (20, 20, 200)
    return Frame(source=source, image_bgr=image, captured_at=1.0)


def make_dark_frame(source: str) -> Frame:
    image = np.zeros((40, 40, 3), dtype=np.uint8)
    return Frame(source=source, image_bgr=image, captured_at=1.0)


def build_profile() -> GameProfile:
    return GameProfile(
        game="Counter-Strike 2",
        events=[
            EventConfig(
                name="player_1_low_health",
                scene="CS2 - Player 1",
                category="survival",
                priority=40,
                hold_sec=0.1,
                stable_frames=1,
                rules=[
                    RegionColorRuleConfig(
                        type="region_color",
                        source="player_1",
                        region=(0, 0, 40, 40),
                        lower_bgr=(0, 0, 120),
                        upper_bgr=(90, 90, 255),
                        min_ratio=0.5,
                    )
                ],
            )
        ],
    )


def test_detection_to_obs_switch_pipeline_live_mode() -> None:
    profile = build_profile()
    evaluator = RuleEvaluator(profile, config_dir=Path("."))
    director = DirectorEngine(
        DirectorConfig(
            default_scene="CS2 - Observer",
            min_confidence=0.75,
            switch_cooldown_sec=0.01,
            fallback_after_sec=0.01,
        )
    )
    router = ObsSceneRouter(ObsConfig(dry_run=False))
    router.client = FakeReqClient(["CS2 - Observer", "CS2 - Player 1"])

    detections = evaluator.evaluate([make_red_hud_frame("player_1")])
    decision = director.decide(detections)

    assert decision is not None
    router.switch_scene(decision.scene, decision.reason)

    assert decision.scene == "CS2 - Player 1"
    assert router.client.switched_to == ["CS2 - Player 1"]


def test_pipeline_dry_run_does_not_touch_client() -> None:
    profile = build_profile()
    evaluator = RuleEvaluator(profile, config_dir=Path("."))
    director = DirectorEngine(
        DirectorConfig(
            default_scene="CS2 - Observer",
            min_confidence=0.75,
            switch_cooldown_sec=0.01,
            fallback_after_sec=0.01,
        )
    )
    router = ObsSceneRouter(ObsConfig(dry_run=True))
    router.connect(expected_scenes={"CS2 - Observer", "CS2 - Player 1"})

    detections = evaluator.evaluate([make_red_hud_frame("player_1")])
    decision = director.decide(detections)

    assert decision is not None
    router.switch_scene(decision.scene, decision.reason)
    assert router.client is None
