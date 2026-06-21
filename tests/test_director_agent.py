from __future__ import annotations

import pytest

from obs_auto_director.matrix.agents.director_agent import DirectorAgent
from obs_auto_director.matrix.buses import MatrixBuses
from obs_auto_director.matrix.clock import set_now_override
from obs_auto_director.matrix.domain import MatchIntelligenceEvent, SlotState
from obs_auto_director.matrix.matrix_core import MatrixCore


@pytest.fixture(autouse=True)
def fixed_clock():
    set_now_override(5000.0)
    yield
    set_now_override(None)


def build():
    matrix = MatrixCore()
    matrix.configure_input("input_01", state=SlotState.ACTIVE)
    buses = MatrixBuses()
    intents = []
    buses.decision.subscribe(lambda intent: intents.append(intent))
    agent = DirectorAgent(buses, matrix, program_output="output_01")
    return matrix, buses, agent, intents


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
