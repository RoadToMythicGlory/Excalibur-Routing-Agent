from __future__ import annotations

from excalibur.matrix.clock import advance_now_override, set_now_override
from excalibur.matrix.domain import ControlAction, ControlCommand
from excalibur.matrix.service import MatrixService
from excalibur.matrix.soccer.domain import CameraAssignment, CameraRole, DirectorMode
from excalibur.matrix.soccer.simulator import event, heartbeat


def _soccer(tmp_path) -> MatrixService:
    set_now_override(5_000)
    return MatrixService(
        obs_dry_run=True,
        config_path=str(tmp_path / "runtime.json"),
        sport_mode="soccer",
    )


def _assign(service: MatrixService, input_id: str, role: str, confidence: float = 0.9) -> None:
    assignment = CameraAssignment(
        input_id=input_id,
        role=role,
        confidence=confidence,
        source="vision",
        sharpness=0.7,
        coverage=0.4,
        visible_ball=True,
    )
    assert service.soccer_cameras is not None
    assert service.soccer_fusion is not None
    service.soccer_cameras.update(assignment)
    service.soccer_fusion.cameras[input_id] = assignment


def _activate(service: MatrixService, payload: dict) -> None:
    service.feed_soccer_events(heartbeat(sequence=payload["sequence"] - 1 if payload["sequence"] else 1))
    service.feed_soccer_events(payload)
    advance_now_override(400)
    assert service.soccer_director is not None
    service.soccer_director.tick()


def test_penalty_prefers_goal_line_then_returns_to_main(tmp_path):
    service = _soccer(tmp_path)
    _assign(service, "input_02", CameraRole.GOAL_LINE.value)
    _activate(
        service,
        event("penalty_kick", sequence=2, phase="execution", event_id="pen-1"),
    )
    assert service.matrix.current_input_for("output_01") == "input_02"
    service.feed_soccer_events(
        event("penalty_kick", revision="end", sequence=3, event_id="pen-1")
    )
    advance_now_override(900)
    assert service.soccer_director is not None
    service.soccer_director.tick()
    advance_now_override(500)
    service.soccer_director.tick()
    assert service.matrix.current_input_for("output_01") == "input_01"
    set_now_override(None)


def test_burst_attack_can_change_angles(tmp_path):
    service = _soccer(tmp_path)
    _assign(service, "input_02", CameraRole.TACTICAL_HIGH.value)
    _assign(service, "input_03", CameraRole.SIDELINE_TIGHT.value)
    _activate(
        service,
        event("burst_attack", sequence=2, phase="setup", event_id="brk", zone="midfield"),
    )
    # Setup phase prefers tactical_high, so the burst opens on input_02.
    assert service.matrix.current_input_for("output_01") == "input_02"
    advance_now_override(2_000)
    service.feed_soccer_events(
        event(
            "burst_attack",
            revision="update",
            sequence=3,
            event_id="brk",
            phase="follow_through",
            zone="attacking_box",
            recommended_inputs=["input_03"],
        )
    )
    # Dwell, cooldown, and score margin are all satisfied: the director must
    # actually cut within the same correlated event, not just stay put.
    assert service.matrix.current_input_for("output_01") == "input_03"
    set_now_override(None)


def test_no_idle_rotation_in_soccer(tmp_path):
    service = _soccer(tmp_path)
    service.director.rotate_idle()
    assert service.matrix.current_input_for("output_01") is None
    assert service.soccer_director is not None
    first = service.soccer_director.tick()
    if first is not None:
        assert first.candidate_input == "input_01"
        assert "return_main" in first.reason
    assert service.matrix.current_input_for("output_01") in {None, "input_01"}
    assert service.soccer_director.tick() is None
    set_now_override(None)


def test_manual_cut_never_auto_resumes(tmp_path):
    path = tmp_path / "runtime.json"
    service = MatrixService(
        obs_dry_run=True, config_path=str(path), sport_mode="soccer"
    )
    _assign(service, "input_02", CameraRole.GOAL_LINE.value)
    service.routing.handle_control(
        ControlCommand(
            action=ControlAction.MANUAL_CUT,
            operator="td",
            target_output="output_01",
            candidate_input="input_03",
            reason="manual_cut",
        )
    )
    assert service.director_mode == DirectorMode.MANUAL_SUSPENDED
    assert service.agent_enabled is False
    assert service.suspension is not None
    assert service.suspension["reason"] == "manual_cut"
    _activate(service, event("penalty_kick", sequence=2, event_id="p"))
    assert service.matrix.current_input_for("output_01") == "input_03"
    reloaded = MatrixService(obs_dry_run=True, config_path=str(path))
    assert reloaded.director_mode == DirectorMode.MANUAL_SUSPENDED
    assert reloaded.suspension is not None
    assert reloaded.suspension["reason"] == "manual_cut"
    reloaded.set_agent_enabled(True)
    assert reloaded.director_mode == DirectorMode.AUTOMATIC
    assert reloaded.suspension is None
    set_now_override(None)
