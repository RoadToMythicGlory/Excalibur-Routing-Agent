from pathlib import Path

import numpy as np

from obs_auto_director.capture.manager import Frame
from obs_auto_director.config import (
    EventConfig,
    GameProfile,
    RegionBrightnessRuleConfig,
    RegionColorRuleConfig,
)
from obs_auto_director.detection.rules import RuleEvaluator


def test_region_color_rule_detects_low_health_red_hud() -> None:
    profile = GameProfile(
        game="Counter-Strike 2",
        events=[
            EventConfig(
                name="player_1_low_health",
                scene="CS2 - Player 1",
                priority=40,
                rules=[
                    RegionColorRuleConfig(
                        type="region_color",
                        source="player_1",
                        region=(0, 0, 20, 20),
                        lower_bgr=(0, 0, 120),
                        upper_bgr=(90, 90, 255),
                        min_ratio=0.5,
                    )
                ],
            )
        ],
    )
    evaluator = RuleEvaluator(profile, config_dir=Path("."))
    image = np.zeros((40, 40, 3), dtype=np.uint8)
    image[0:20, 0:20] = (20, 20, 200)

    detections = evaluator.evaluate(
        [Frame(source="player_1", image_bgr=image, captured_at=1.0)]
    )

    assert len(detections) == 1
    assert detections[0].event_name == "player_1_low_health"
    assert detections[0].confidence == 1.0
    assert detections[0].category == "general"


def test_all_rule_mode_requires_every_rule_to_match() -> None:
    profile = GameProfile(
        game="Counter-Strike 2",
        events=[
            EventConfig(
                name="player_1_molotov_fire_impact",
                category="utility",
                scene="CS2 - Player 1",
                priority=65,
                rule_mode="all",
                rules=[
                    RegionColorRuleConfig(
                        type="region_color",
                        source="player_1",
                        region=(0, 0, 20, 20),
                        lower_bgr=(0, 70, 150),
                        upper_bgr=(90, 210, 255),
                        min_ratio=0.5,
                    ),
                    RegionBrightnessRuleConfig(
                        type="region_brightness",
                        source="player_1",
                        region=(20, 20, 20, 20),
                        operator="above",
                        threshold=200,
                    ),
                ],
            )
        ],
    )
    evaluator = RuleEvaluator(profile, config_dir=Path("."))
    image = np.zeros((40, 40, 3), dtype=np.uint8)
    image[0:20, 0:20] = (20, 120, 220)

    detections = evaluator.evaluate(
        [Frame(source="player_1", image_bgr=image, captured_at=1.0)]
    )

    assert detections == []

    image[20:40, 20:40] = (255, 255, 255)
    detections = evaluator.evaluate(
        [Frame(source="player_1", image_bgr=image, captured_at=2.0)]
    )

    assert len(detections) == 1
    assert detections[0].event_name == "player_1_molotov_fire_impact"
    assert detections[0].category == "utility"
