from __future__ import annotations

import pytest

from obs_auto_director.matrix.clock import set_now_override
from obs_auto_director.matrix.demo import run_clutch_demo


@pytest.fixture(autouse=True)
def reset_clock():
    yield
    set_now_override(None)


def test_clutch_demo_routes_program_to_hero_input():
    service = run_clutch_demo(obs_dry_run=True)
    program = service.matrix.current_input_for(service.program_output)
    assert program == "input_01"
    assert service.obs.current_scene == "PGM - Player 01"


def test_clutch_demo_emits_clutch_story():
    service = run_clutch_demo(obs_dry_run=True)
    snapshot = service.state_snapshot()
    types = {event["type"] for event in snapshot["intelligence"]}
    assert "potential_clutch" in types


def test_clutch_demo_records_audit_and_accepts_routes():
    service = run_clutch_demo(obs_dry_run=True)
    snapshot = service.state_snapshot()
    outcomes = {row["outcome"] for row in snapshot["audit"]}
    assert "accepted" in outcomes


def test_state_snapshot_exposes_eleven_slots():
    service = run_clutch_demo(obs_dry_run=True)
    snapshot = service.state_snapshot()
    assert len(snapshot["matrix"]["inputs"]) == 11
    assert len(snapshot["matrix"]["outputs"]) == 11
