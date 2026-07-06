from excalibur.config import DirectorConfig
from excalibur.detection.rules import Detection
from excalibur.director.engine import DirectorEngine


def make_detection(
    event_name: str,
    scene: str,
    priority: int,
    stable_frames: int = 1,
    confidence: float = 0.9,
    category: str = "combat",
    interrupt: bool = False,
    hold_sec: float = 1.0,
) -> Detection:
    return Detection(
        event_name=event_name,
        source="player_1",
        confidence=confidence,
        priority=priority,
        scene=scene,
        category=category,
        description=None,
        hold_sec=hold_sec,
        stable_frames=stable_frames,
        interrupt=interrupt,
        detected_at=1.0,
    )


def test_director_selects_highest_priority_stable_event() -> None:
    director = DirectorEngine(
        DirectorConfig(
            default_scene="CS2 - Observer",
            min_confidence=0.75,
            switch_cooldown_sec=0.1,
            fallback_after_sec=1.0,
        )
    )

    decision = director.decide(
        [
            make_detection("active_combat", "CS2 - Player 1", priority=20),
            make_detection("low_health", "CS2 - Player 2", priority=40),
        ]
    )

    assert decision is not None
    assert decision.scene == "CS2 - Player 2"
    assert decision.event_name == "low_health"


def test_director_requires_stable_frames() -> None:
    director = DirectorEngine(
        DirectorConfig(
            default_scene="CS2 - Observer",
            min_confidence=0.75,
            switch_cooldown_sec=0.1,
            fallback_after_sec=1.0,
        )
    )
    detection = make_detection(
        "active_combat",
        "CS2 - Player 1",
        priority=20,
        stable_frames=2,
    )

    assert director.decide([detection]) is None
    assert director.decide([detection]) is not None


def test_director_holds_scene_against_small_priority_gap() -> None:
    director = DirectorEngine(
        DirectorConfig(
            default_scene="CS2 - Observer",
            min_confidence=0.75,
            switch_cooldown_sec=0.1,
            fallback_after_sec=1.0,
            preempt_priority_gap=25,
        )
    )

    first = make_detection(
        "molotov_throw",
        "CS2 - Player 1",
        priority=70,
        category="utility",
        hold_sec=10.0,
    )
    second = make_detection(
        "sniper_action",
        "CS2 - Player 2",
        priority=80,
        category="sniper",
    )

    assert director.decide([first]) is not None
    assert director.decide([second]) is None
    assert director.current_scene == "CS2 - Player 1"


def test_director_allows_interrupt_to_preempt_hold() -> None:
    director = DirectorEngine(
        DirectorConfig(
            default_scene="CS2 - Observer",
            min_confidence=0.75,
            switch_cooldown_sec=0.1,
            fallback_after_sec=1.0,
            preempt_priority_gap=25,
        )
    )

    first = make_detection(
        "active_combat",
        "CS2 - Player 1",
        priority=40,
        hold_sec=10.0,
    )
    urgent = make_detection(
        "critical_survival",
        "CS2 - Player 2",
        priority=90,
        category="survival",
        interrupt=True,
    )

    assert director.decide([first]) is not None
    decision = director.decide([urgent])

    assert decision is not None
    assert decision.scene == "CS2 - Player 2"
    assert decision.category == "survival"
    assert decision.priority == 90
