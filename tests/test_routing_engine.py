from __future__ import annotations

import pytest

from obs_auto_director.matrix.buses import MatrixBuses
from obs_auto_director.matrix.clock import set_now_override
from obs_auto_director.matrix.domain import (
    BusName,
    ControlAction,
    ControlCommand,
    DecisionOutcome,
    RoutingIntent,
    SlotState,
)
from obs_auto_director.matrix.matrix_core import MatrixCore
from obs_auto_director.matrix.routing_engine import RoutingEngine


@pytest.fixture
def setup():
    set_now_override(10_000.0)
    matrix = MatrixCore()
    for slot in ("input_01", "input_02", "input_03"):
        matrix.configure_input(slot, state=SlotState.ACTIVE)
    buses = MatrixBuses()
    engine = RoutingEngine(matrix, buses, switch_cooldown_ms=600.0, preempt_priority_gap=15)
    yield matrix, buses, engine
    set_now_override(None)


def make_intent(candidate, priority=50, ttl_ms=3000.0, interrupt=False, created_at=10_000.0):
    return RoutingIntent(
        producer="director-agent",
        bus=BusName.DECISION,
        target_output="output_01",
        candidate_input=candidate,
        reason="story:test",
        priority=priority,
        ttl_ms=ttl_ms,
        interrupt=interrupt,
        created_at=created_at,
    )


def test_accepts_fresh_intent(setup):
    matrix, _, engine = setup
    decision = engine.handle_intent(make_intent("input_01"))
    assert decision.outcome == DecisionOutcome.ACCEPTED
    assert matrix.current_input_for("output_01") == "input_01"


def test_rejects_stale_intent(setup):
    _, _, engine = setup
    set_now_override(20_000.0)
    decision = engine.handle_intent(make_intent("input_01", ttl_ms=1000.0, created_at=10_000.0))
    assert decision.outcome == DecisionOutcome.REJECTED
    assert "stale_intent" in decision.reason


def test_rejects_unavailable_source(setup):
    _, _, engine = setup
    decision = engine.handle_intent(make_intent("input_07"))
    assert decision.outcome == DecisionOutcome.REJECTED
    assert "source_unavailable" in decision.reason


def test_lock_blocks_agent_intent(setup):
    _, _, engine = setup
    engine.handle_control(
        ControlCommand(
            action=ControlAction.LOCK_OUTPUT,
            operator="op",
            target_output="output_01",
            reason="protect",
        )
    )
    decision = engine.handle_intent(make_intent("input_01"))
    assert decision.outcome == DecisionOutcome.REJECTED
    assert "output_locked_by" in decision.reason


def test_cooldown_delays_rapid_switch(setup):
    _, _, engine = setup
    engine.handle_intent(make_intent("input_01"))
    set_now_override(10_100.0)  # within 600ms cooldown
    decision = engine.handle_intent(make_intent("input_02", created_at=10_100.0))
    assert decision.outcome == DecisionOutcome.DELAYED
    assert decision.reason == "switch_cooldown"


def test_high_priority_interrupt_preempts_hold(setup):
    matrix, _, engine = setup
    held = make_intent("input_01", priority=50)
    held.hold_ms = 5000.0
    engine.handle_intent(held)
    set_now_override(10_200.0)
    strong = make_intent("input_02", priority=95, interrupt=True, created_at=10_200.0)
    decision = engine.handle_intent(strong)
    assert decision.outcome == DecisionOutcome.ACCEPTED
    assert matrix.current_input_for("output_01") == "input_02"


def test_emergency_route_overrides_lock(setup):
    matrix, _, engine = setup
    engine.handle_control(
        ControlCommand(
            action=ControlAction.LOCK_OUTPUT,
            operator="op1",
            target_output="output_01",
        )
    )
    decision = engine.handle_control(
        ControlCommand(
            action=ControlAction.EMERGENCY_ROUTE,
            operator="op2",
            target_output="output_01",
            candidate_input="input_03",
            reason="panic",
        )
    )
    assert decision.outcome == DecisionOutcome.ACCEPTED
    assert matrix.current_input_for("output_01") == "input_03"
