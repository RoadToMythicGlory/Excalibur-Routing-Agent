from __future__ import annotations

import pytest

from excalibur.matrix.clock import set_now_override
from excalibur.matrix.domain import (
    BusName,
    RoutingIntent,
    SlotState,
    input_slot_id,
    output_slot_id,
)
from excalibur.matrix.matrix_core import MatrixCore


@pytest.fixture(autouse=True)
def fixed_clock():
    set_now_override(1000.0)
    yield
    set_now_override(None)


def test_matrix_defaults_to_eleven_slots():
    matrix = MatrixCore()
    assert len(matrix.inputs) == 11
    assert len(matrix.outputs) == 11
    assert input_slot_id(1) in matrix.inputs
    assert output_slot_id(11) in matrix.outputs


def test_matrix_honors_explicit_counts():
    matrix = MatrixCore(inputs=4, outputs=6)
    assert len(matrix.inputs) == 4
    assert len(matrix.outputs) == 6
    assert input_slot_id(4) in matrix.inputs
    assert input_slot_id(5) not in matrix.inputs
    assert output_slot_id(6) in matrix.outputs
    assert output_slot_id(7) not in matrix.outputs


def test_matrix_resize_grows_and_shrinks_trailing_slots():
    matrix = MatrixCore(inputs=2, outputs=2)
    matrix.add_input()
    assert input_slot_id(3) in matrix.inputs
    assert matrix.remove_last_input() == input_slot_id(3)
    assert input_slot_id(3) not in matrix.inputs


def test_matrix_refuses_to_remove_routed_input():
    matrix = MatrixCore(inputs=3, outputs=3)
    matrix.configure_input("input_03", state=SlotState.ACTIVE)
    matrix.apply_route("output_01", "input_03", set_by="test", reason="manual")
    with pytest.raises(ValueError):
        matrix.remove_last_input()


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
