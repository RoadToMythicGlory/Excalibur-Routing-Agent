import time
from pathlib import Path

import numpy as np

from excalibur.capture.manager import Frame
from excalibur.config import (
    AliveCountRuleConfig,
    CutRuleConfig,
    DirectorConfig,
    EventConfig,
    GameProfile,
    KillFeedChangeRuleConfig,
    ScoringConfig,
    SourceProfile,
    load_game_profile,
)
from excalibur.detection.rules import Detection, RuleEvaluator
from excalibur.director.engine import DirectorEngine


ROOT = Path(__file__).resolve().parents[1]


def test_expanded_cs2_profile_loads_all_sections() -> None:
    profile = load_game_profile(ROOT / "configs" / "games" / "cs2.yaml")

    assert profile.version == "1.0.0"
    assert profile.defaults.fallback_scene == "CS2 - Observer"
    assert profile.defaults.min_cut_gap_sec == 1.2
    assert profile.matrix is not None and profile.matrix.enabled is True
    assert len(profile.sources) == 11
    assert profile.scene_for_source("player_7") == "CS2 - Player 7"
    assert profile.scoring.weights["triple_kill_detected"] == 220
    assert {rule.type for rule in profile.cut_rules} == {
        "cooldown",
        "interrupt_priority",
        "score_margin",
        "fallback",
        "source_health_gate",
    }
    # 10 observer events + 80 per-player events.
    assert len(profile.observer_events) == 10
    assert len(profile.events) == 80
    assert len(profile.all_events) == 90


def _observer_profile() -> GameProfile:
    return GameProfile(
        game="CS2",
        sources={
            "observer": SourceProfile(kind="observer", scene="CS2 - Observer"),
            "player_1": SourceProfile(kind="player_pov", scene="CS2 - Player 1"),
        },
        observer_events=[
            EventConfig(
                name="kill_feed_detected",
                category="kill",
                source="observer",
                target_source="dynamic",
                scene="dynamic",
                priority=100,
                stable_frames=1,
                interrupt=True,
                rules=[
                    KillFeedChangeRuleConfig(
                        type="kill_feed_change",
                        source="observer",
                        region=(0, 0, 40, 40),
                        min_new_entries=1,
                    )
                ],
            )
        ],
    )


def test_kill_feed_change_fires_on_region_change_and_resolves_dynamic_scene() -> None:
    profile = _observer_profile()
    evaluator = RuleEvaluator(profile, config_dir=Path("."))

    base = np.zeros((40, 40, 3), dtype=np.uint8)
    assert evaluator.evaluate([Frame("observer", base, captured_at=1.0)]) == []

    changed = np.full((40, 40, 3), 255, dtype=np.uint8)
    detections = evaluator.evaluate([Frame("observer", changed, captured_at=2.0)])

    assert len(detections) == 1
    detection = detections[0]
    assert detection.event_name == "kill_feed_detected"
    # "dynamic" routing resolves to the firing feed's scene (observer here).
    assert detection.scene == "CS2 - Observer"
    assert detection.target_source == "observer"


def test_alive_count_rule_is_dormant_until_counts_injected() -> None:
    profile = GameProfile(
        game="CS2",
        sources={"observer": SourceProfile(kind="observer", scene="CS2 - Observer")},
        observer_events=[
            EventConfig(
                name="clutch_1v1",
                category="clutch",
                source="observer",
                scene="CS2 - Observer",
                priority=125,
                stable_frames=1,
                rules=[
                    AliveCountRuleConfig(
                        type="alive_count",
                        source="observer",
                        team_a_alive=1,
                        team_b_alive=1,
                    )
                ],
            )
        ],
    )
    evaluator = RuleEvaluator(profile, config_dir=Path("."))
    frame = Frame("observer", np.zeros((40, 40, 3), dtype=np.uint8), captured_at=1.0)

    assert evaluator.evaluate([frame]) == []

    evaluator.set_alive_counts((1, 1))
    detections = evaluator.evaluate([frame])
    assert len(detections) == 1
    assert detections[0].event_name == "clutch_1v1"

    evaluator.set_alive_counts((1, 3))
    assert evaluator.evaluate([frame]) == []


def _scoring_detection(
    event_name: str,
    scene: str,
    category: str,
    priority: int,
    *,
    target_source: str | None = None,
    interrupt: bool = False,
    hold_sec: float = 1.0,
) -> Detection:
    return Detection(
        event_name=event_name,
        source="observer",
        confidence=0.9,
        priority=priority,
        scene=scene,
        category=category,
        description=None,
        hold_sec=hold_sec,
        stable_frames=1,
        interrupt=interrupt,
        detected_at=time.time(),
        target_source=target_source,
    )


def _director_with(profile_kwargs: dict) -> DirectorEngine:
    profile = GameProfile(
        game="CS2",
        events=[
            EventConfig(
                name="placeholder",
                scene="CS2 - Observer",
                rules=[
                    AliveCountRuleConfig(type="alive_count", source="observer", team_a_alive=0, team_b_alive=0)
                ],
            )
        ],
        **profile_kwargs,
    )
    config = DirectorConfig(
        default_scene="CS2 - Observer",
        min_confidence=0.75,
        switch_cooldown_sec=0.001,
        fallback_after_sec=10.0,
        preempt_priority_gap=25,
    )
    return DirectorEngine(config, profile=profile)


def test_scoring_weights_override_raw_priority_in_ranking() -> None:
    director = _director_with(
        {
            "scoring": ScoringConfig(
                enabled=True,
                weights={"kill_feed_detected": 120, "active_combat": 25},
            )
        }
    )

    # active_combat has the higher raw priority but the lower weighted score.
    decision = director.decide(
        [
            _scoring_detection("active_combat", "CS2 - Player 1", "combat", priority=200),
            _scoring_detection("kill_feed_detected", "CS2 - Player 2", "kill", priority=10),
        ]
    )

    assert decision is not None
    assert decision.scene == "CS2 - Player 2"
    assert decision.event_name == "kill_feed_detected"


def test_score_margin_holds_current_source_when_scores_are_close() -> None:
    director = _director_with(
        {
            "scoring": ScoringConfig(enabled=True, weights={"active_combat": 25}),
            "cut_rules": [
                CutRuleConfig(name="margin", type="score_margin", margin=15)
            ],
        }
    )

    first = director.decide(
        [_scoring_detection("active_combat", "CS2 - Player 1", "combat", priority=40)]
    )
    assert first is not None and first.scene == "CS2 - Player 1"

    # A different feed with an equal score should not win the close call.
    second = director.decide(
        [_scoring_detection("active_combat", "CS2 - Player 2", "combat", priority=40)]
    )
    assert second is None
    assert director.current_scene == "CS2 - Player 1"


def test_source_health_gate_blocks_cuts_to_unhealthy_feed() -> None:
    director = _director_with(
        {
            "scoring": ScoringConfig(enabled=True, weights={"active_combat": 25}),
            "cut_rules": [
                CutRuleConfig(
                    name="health", type="source_health_gate", require_healthy=True
                )
            ],
        }
    )
    director.set_source_health("player_2", healthy=False)

    decision = director.decide(
        [
            _scoring_detection(
                "active_combat",
                "CS2 - Player 2",
                "combat",
                priority=40,
                target_source="player_2",
            )
        ]
    )
    assert decision is None
    assert director.current_scene == "CS2 - Observer"


def test_interrupt_priority_cut_rule_preempts_active_hold() -> None:
    director = _director_with(
        {
            "scoring": ScoringConfig(
                enabled=True, weights={"active_combat": 25, "defuse_started": 170}
            ),
            "cut_rules": [
                CutRuleConfig(
                    name="interrupt", type="interrupt_priority", min_priority=90
                )
            ],
        }
    )

    first = director.decide(
        [
            _scoring_detection(
                "active_combat", "CS2 - Player 1", "combat", priority=40, hold_sec=30.0
            )
        ]
    )
    assert first is not None

    # priority 120 >= interrupt_priority 90, so it preempts the long hold even
    # though the event is not flagged interrupt itself.
    second = director.decide(
        [
            _scoring_detection(
                "defuse_started", "CS2 - Observer", "objective", priority=120
            )
        ]
    )
    assert second is not None
    assert second.scene == "CS2 - Observer"
