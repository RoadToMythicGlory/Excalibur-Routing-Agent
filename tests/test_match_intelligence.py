from __future__ import annotations

import pytest

from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import set_now_override
from excalibur.matrix.gsi.receiver import GsiReceiver
from excalibur.matrix.gsi.simulator import ROSTER, clutch_round_sequence
from excalibur.matrix.intelligence.engine import MatchIntelligenceEngine


@pytest.fixture(autouse=True)
def fixed_clock():
    set_now_override(1_700_000_000_000.0)
    yield
    set_now_override(None)


def run_sequence():
    mapping = {steamid: f"input_{i + 1:02d}" for i, (steamid, _, _) in enumerate(ROSTER)}
    buses = MatrixBuses()
    stories: list[str] = []
    buses.intelligence.subscribe(lambda event: stories.append(event.type))
    receiver = GsiReceiver(buses, steam_to_input=mapping)
    engine = MatchIntelligenceEngine(buses)
    for payload in clutch_round_sequence():
        state = receiver.ingest(payload)
        engine.update(state)
    return stories, buses


def test_detects_clutch_and_duel_and_execute():
    stories, _ = run_sequence()
    assert "awp_duel" in stories
    assert "site_execute" in stories
    assert "potential_clutch" in stories
    assert "last_alive" in stories
    assert "retake_started" in stories


def test_detects_kill_and_bomb_drama_stories():
    stories, buses = run_sequence()
    assert "opening_kill" in stories
    assert "multi_kill" in stories
    assert "defuse_drama" in stories
    assert "bomb_last_seconds" in stories

    multi = next(
        e for e in buses.intelligence.recent(50) if e.type == "multi_kill"
    )
    assert multi.priority == 84  # double kill
    assert multi.recommended_inputs == ["input_06"]

    defuse = next(
        e for e in buses.intelligence.recent(50) if e.type == "defuse_drama"
    )
    assert defuse.priority == 95
    assert defuse.recommended_inputs == ["input_01"]  # the defuser, not all CTs


def test_clutch_event_has_evidence_and_recommended_inputs():
    _, buses = run_sequence()
    clutch = next(
        event
        for event in buses.intelligence.recent(50)
        if event.type == "potential_clutch"
    )
    assert clutch.priority == 92
    assert clutch.recommended_inputs == ["input_01"]
    assert any("last_alive" in item for item in clutch.evidence)


def test_story_is_edge_triggered_once_per_round():
    _, buses = run_sequence()
    clutches = [e for e in buses.intelligence.recent(50) if e.type == "potential_clutch"]
    assert len(clutches) == 1


def _state(round_id: str, alive_ct: int, alive_t: int, phase: str = "live"):
    from excalibur.matrix.domain import MatchState, PlayerState

    players = {}
    for i in range(5):
        slot = f"input_{i + 1:02d}"
        players[slot] = PlayerState(
            slot=slot, name=f"CT {i + 1}", team="CT", health=100 if i < alive_ct else 0,
            alive=i < alive_ct, input_id=slot,
        )
    for i in range(5):
        slot = f"input_{i + 6:02d}"
        players[slot] = PlayerState(
            slot=slot, name=f"T {i + 1}", team="T", health=100 if i < alive_t else 0,
            alive=i < alive_t, input_id=slot,
        )
    return MatchState(round_id=round_id, phase=phase, players=players)


def test_story_rearms_when_condition_breaks_and_returns():
    buses = MatrixBuses()
    engine = MatchIntelligenceEngine(buses)
    engine.update(_state("r1", alive_ct=1, alive_t=3))  # clutch starts
    engine.update(_state("r1", alive_ct=2, alive_t=3))  # condition breaks
    engine.update(_state("r1", alive_ct=1, alive_t=3))  # clutch again
    clutches = [e for e in buses.intelligence.recent(50) if e.type == "potential_clutch"]
    assert len(clutches) == 2
    assert [e.lifecycle for e in clutches] == ["start", "start"]


def test_round_over_phase_silences_stories():
    buses = MatrixBuses()
    engine = MatchIntelligenceEngine(buses)
    engine.update(_state("r1", alive_ct=1, alive_t=3))
    events = engine.update(_state("r1", alive_ct=1, alive_t=3, phase="over"))
    assert events == []
