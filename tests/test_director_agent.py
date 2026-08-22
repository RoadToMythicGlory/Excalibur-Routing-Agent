from __future__ import annotations

import pytest

from excalibur.matrix.agents.director_agent import DirectorAgent
from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import set_now_override
from excalibur.matrix.domain import (
    GameStateEvent,
    MatchIntelligenceEvent,
    MatchState,
    PlayerState,
    SlotState,
)
from excalibur.matrix.matrix_core import MatrixCore


@pytest.fixture(autouse=True)
def fixed_clock():
    set_now_override(5000.0)
    yield
    set_now_override(None)


def build(active_inputs: list[str] | None = None, state: MatchState | None = None):
    matrix = MatrixCore()
    for slot_id in active_inputs or ["input_01"]:
        matrix.configure_input(slot_id, state=SlotState.ACTIVE)
    buses = MatrixBuses()
    intents = []
    buses.decision.subscribe(lambda intent: intents.append(intent))
    agent = DirectorAgent(
        buses,
        matrix,
        program_output="output_01",
        state_provider=(lambda: state) if state is not None else None,
    )
    return matrix, buses, agent, intents


def _player(slot: str, team: str = "CT", health: int = 100, alive: bool = True,
            round_kills: int = 0) -> PlayerState:
    return PlayerState(
        slot=slot, name=slot, team=team, health=health, alive=alive,
        round_kills=round_kills, input_id=slot,
    )


def test_story_produces_decision_intent():
    _, buses, _, intents = build()
    buses.intelligence.publish(
        MatchIntelligenceEvent(
            type="potential_clutch",
            round_id="r1",
            priority=92,
            confidence=0.9,
            ttl_ms=2500,
            affected_players=["input_01"],
            recommended_inputs=["input_01"],
            evidence=["x"],
        )
    )
    assert len(intents) == 1
    assert intents[0].candidate_input == "input_01"
    assert intents[0].interrupt is True


def test_story_with_unavailable_input_produces_no_intent():
    _, buses, _, intents = build()
    buses.intelligence.publish(
        MatchIntelligenceEvent(
            type="site_execute",
            round_id="r1",
            priority=75,
            confidence=0.8,
            ttl_ms=2500,
            affected_players=["input_07"],
            recommended_inputs=["input_07"],
            evidence=["x"],
        )
    )
    assert intents == []


def test_stale_story_ignored():
    _, buses, _, intents = build()
    event = MatchIntelligenceEvent(
        type="potential_clutch",
        round_id="r1",
        priority=92,
        confidence=0.9,
        ttl_ms=100,
        affected_players=["input_01"],
        recommended_inputs=["input_01"],
        evidence=["x"],
        created_at=1000.0,
    )
    buses.intelligence.publish(event)  # now is 5000, age 4000 > ttl 100
    assert intents == []


def test_kill_event_cuts_to_killer_with_escalating_priority():
    _, buses, _, intents = build()
    buses.game_state.publish(
        GameStateEvent(
            type="player_kill",
            player_slot="input_01",
            data={"round_kills": 1},
            round_id="r1",
        )
    )
    assert len(intents) == 1
    assert intents[0].candidate_input == "input_01"
    assert intents[0].priority == 65
    assert intents[0].interrupt is False

    buses.game_state.publish(
        GameStateEvent(
            type="player_kill",
            player_slot="input_01",
            data={"round_kills": 3},
            round_id="r1",
        )
    )
    assert intents[1].priority == 87
    assert intents[1].interrupt is True


def test_player_death_no_longer_cuts_to_victim_pov():
    _, buses, _, intents = build()
    buses.game_state.publish(
        GameStateEvent(
            type="player_death", player_slot="input_01", data={}, round_id="r1"
        )
    )
    assert intents == []


def test_story_candidate_ranked_by_drama_not_list_order():
    state = MatchState(
        round_id="r1",
        phase="live",
        players={
            "input_01": _player("input_01", alive=False),
            "input_02": _player("input_02", health=18, round_kills=2),
        },
    )
    _, buses, _, intents = build(
        active_inputs=["input_01", "input_02"], state=state
    )
    buses.intelligence.publish(
        MatchIntelligenceEvent(
            type="retake_started",
            round_id="r1",
            priority=78,
            confidence=0.7,
            ttl_ms=3000,
            affected_players=["input_01", "input_02"],
            recommended_inputs=["input_01", "input_02"],
            evidence=["x"],
        )
    )
    # input_01 is listed first but dead; the low-health killer gets the cut.
    assert len(intents) == 1
    assert intents[0].candidate_input == "input_02"


def test_idle_rotation_defers_to_recent_story_hold():
    _, buses, agent, intents = build(active_inputs=["input_01", "input_02"])
    buses.intelligence.publish(
        MatchIntelligenceEvent(
            type="potential_clutch",
            round_id="r1",
            priority=92,
            confidence=0.9,
            ttl_ms=2500,
            affected_players=["input_01"],
            recommended_inputs=["input_01"],
            evidence=["x"],
        )
    )
    assert len(intents) == 1
    assert agent.rotate_idle() is None  # story still owns the screen

    set_now_override(5000.0 + 10_000.0)  # hold + grace elapsed
    assert agent.rotate_idle() is not None


def test_idle_rotation_skips_dead_player_feeds():
    state = MatchState(
        round_id="r1",
        phase="live",
        players={
            "input_01": _player("input_01", alive=False),
            "input_02": _player("input_02"),
        },
    )
    _, _, agent, _ = build(active_inputs=["input_01", "input_02"], state=state)
    intent = agent.rotate_idle()
    assert intent is not None
    assert intent.candidate_input == "input_02"
