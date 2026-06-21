from __future__ import annotations

import pytest

from obs_auto_director.matrix.clock import set_now_override
from obs_auto_director.matrix.domain import (
    BusName,
    RoutingIntent,
    SlotState,
    input_slot_id,
    output_slot_id,
)
from obs_auto_director.matrix.matrix_core import MatrixCore


@pytest.fixture(autouse=True)
def fixed_clock():
    set_now_override(1000.0)
    yield
    set_now_override(None)


def test_matrix_always_has_eleven_slots():
    matrix = MatrixCore(inputs=3, outputs=3)
    assert len(matrix.inputs) == 11
    assert len(matrix.outputs) == 11
    assert input_slot_id(1) in matrix.inputs
    assert output_slot_id(11) in matrix.outputs


def test_input_available_requires_signal():
    matrix = MatrixCore()
    assert not matrix.input_available("input_01")
    matrix.configure_input("input_01", state=SlotState.ACTIVE)
    assert matrix.input_available("input_01")
    matrix.configure_input("input_01", state=SlotState.MISSING_SIGNAL)
    assert not matrix.input_available("input_01")


def test_apply_route_records_current_input():
    matrix = MatrixCore()
    matrix.configure_input("input_02", state=SlotState.ACTIVE)
    matrix.apply_route("output_01", "input_02", set_by="test", reason="manual")
    assert matrix.current_input_for("output_01") == "input_02"


def test_intent_staleness_uses_ttl():
    intent = RoutingIntent(
        producer="director-agent",
        bus=BusName.DECISION,
        target_output="output_01",
        candidate_input="input_01",
        reason="story:test",
        ttl_ms=500.0,
        created_at=1000.0,
    )
    assert not intent.is_stale(1400.0)
    assert intent.is_stale(1600.0)
