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
