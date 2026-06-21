from __future__ import annotations

import pytest

from obs_auto_director.matrix.clock import advance_now_override, set_now_override
from obs_auto_director.matrix.service import MatrixService


@pytest.fixture(autouse=True)
def reset_clock():
    set_now_override(1_000.0)
    yield
    set_now_override(None)


def _service(tmp_path) -> MatrixService:
    # Isolated config: default service keeps input_01..03 ACTIVE (SRT).
    return MatrixService(obs_dry_run=True, config_path=str(tmp_path / "runtime.json"))


def test_agent_enabled_by_default_and_in_snapshot(tmp_path):
    service = _service(tmp_path)
    assert service.agent_enabled is True
    assert service.state_snapshot()["agent_enabled"] is True


def test_set_agent_enabled_persists_across_restart(tmp_path):
    path = str(tmp_path / "runtime.json")
    service = MatrixService(obs_dry_run=True, config_path=path)

    assert service.set_agent_enabled(False) is False
    assert service.agent_enabled is False

    reloaded = MatrixService(obs_dry_run=True, config_path=path)
    assert reloaded.agent_enabled is False


def test_idle_rotation_cycles_through_live_inputs(tmp_path):
    service = _service(tmp_path)

    seen: list[str | None] = []
    for _ in range(4):
        service.director.rotate_idle()
        # Clear the per-output hold + cooldown before the next rotation.
        advance_now_override(service.rotate_interval_ms + 1)
        seen.append(service.matrix.current_input_for(service.program_output))

    assert set(seen) >= {"input_01", "input_02", "input_03"}


def test_disabled_agent_neither_rotates_nor_reacts(tmp_path):
    service = _service(tmp_path)
    service.set_agent_enabled(False)

    assert service.director.rotate_idle() is None
    advance_now_override(service.rotate_interval_ms + 1)
    assert service.matrix.current_input_for(service.program_output) is None

    # Story-level intelligence is ignored while the agent is off.
    from obs_auto_director.matrix.domain import MatchIntelligenceEvent

    event = MatchIntelligenceEvent(
        type="potential_clutch",
        round_id="r1",
        priority=92,
        confidence=0.9,
        ttl_ms=2500,
        recommended_inputs=["input_02"],
    )
    assert service.director.on_intelligence(event) is None
