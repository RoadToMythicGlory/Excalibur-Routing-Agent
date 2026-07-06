from __future__ import annotations

import pytest

from excalibur.matrix.clock import set_now_override
from excalibur.matrix.demo import run_clutch_demo


@pytest.fixture(autouse=True)
def reset_clock():
    yield
    set_now_override(None)


def _demo(tmp_path):
    # Isolate from the operator's live runtime config so the simulated SRT
    # inputs (input_01..03) stay ACTIVE instead of being overridden by a
    # file/SRT source from configs/matrix.runtime.json.
    return run_clutch_demo(
        obs_dry_run=True, config_path=str(tmp_path / "runtime.json")
    )


def test_clutch_demo_routes_program_to_hero_input(tmp_path):
    service = _demo(tmp_path)
    program = service.matrix.current_input_for(service.program_output)
    assert program == "input_01"
    assert service.obs.current_scene == "PGM - Player 01"


def test_clutch_demo_emits_clutch_story(tmp_path):
    service = _demo(tmp_path)
    snapshot = service.state_snapshot()
    types = {event["type"] for event in snapshot["intelligence"]}
    assert "potential_clutch" in types


def test_clutch_demo_records_audit_and_accepts_routes(tmp_path):
    service = _demo(tmp_path)
    snapshot = service.state_snapshot()
    outcomes = {row["outcome"] for row in snapshot["audit"]}
    assert "accepted" in outcomes


def test_state_snapshot_exposes_eleven_slots_by_default(tmp_path):
    service = _demo(tmp_path)
    snapshot = service.state_snapshot()
    assert len(snapshot["matrix"]["inputs"]) == 11
    assert len(snapshot["matrix"]["outputs"]) == 11
