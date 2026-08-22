from __future__ import annotations

from excalibur.cv.inference.engine import ModelManifest, vision_routing_allowed
from excalibur.cv.tasks.event_detector import TemporalEventBuffer, infer_event_observation
from excalibur.matrix.clock import advance_now_override, set_now_override
from excalibur.matrix.service import MatrixService
from excalibur.matrix.soccer.config import load_soccer_profile
from excalibur.matrix.soccer.domain import CameraRole, SoccerObservationKind
from excalibur.matrix.soccer.simulator import event, heartbeat


def test_conflicting_sources_prefer_structured_isolated(tmp_path):
    set_now_override(2_000)
    service = MatrixService(
        obs_dry_run=True,
        config_path=str(tmp_path / "rt.json"),
        sport_mode="soccer",
    )
    service.feed_soccer_events(heartbeat(sequence=1))
    service.feed_soccer_events(event("foul", sequence=2, event_id="f"))
    advance_now_override(400)
    assert service.soccer_fusion is not None
    service.soccer_fusion.tick()
    assert service.soccer_fusion.event is not None
    assert service.soccer_fusion.event.event_type == "foul"
    from excalibur.matrix.soccer.domain import VisionObservation

    service.soccer_fusion.ingest_vision(
        VisionObservation(
            kind=SoccerObservationKind.EVENT.value,
            event_type="penalty_kick",
            confidence=0.99,
        )
    )
    assert service.soccer_fusion.event.event_type == "foul"
    set_now_override(None)


def test_untrusted_sources_stay_on_main(tmp_path):
    set_now_override(2_000)
    service = MatrixService(
        obs_dry_run=True,
        config_path=str(tmp_path / "rt.json"),
        sport_mode="soccer",
    )
    service.matrix.apply_route("output_01", "input_02", set_by="test", reason="setup")
    advance_now_override(4_000)
    assert service.soccer_director is not None
    service.soccer_director.tick()
    assert service.matrix.current_input_for("output_01") == "input_01"
    set_now_override(None)


def test_temporal_buffer_treats_passes_as_negative():
    buffer = TemporalEventBuffer(window=3)
    from excalibur.cv.inference.engine import InferenceResult

    for _ in range(3):
        averaged = buffer.update(
            InferenceResult(
                labels={"event:ordinary_pass": 0.9, "event:burst_attack": 0.2},
                latency_ms=3,
            )
        )
    observation = infer_event_observation(averaged, 0.62)
    assert observation.kind == SoccerObservationKind.ORDINARY_PASS.value


def test_missing_model_stays_in_shadow():
    profile = load_soccer_profile()
    manifest = ModelManifest(
        version="none",
        path=None,
        classes=[],
        validated=False,
        metrics={},
    )
    assert vision_routing_allowed(profile, None) is False
    assert vision_routing_allowed(profile, manifest) is False


def test_sport_isolation_blocks_cross_feeds(tmp_path):
    soccer = MatrixService(
        obs_dry_run=True,
        config_path=str(tmp_path / "soccer.json"),
        sport_mode="soccer",
    )
    soccer.feed_gsi({"map": {"name": "de_dust2"}})
    assert soccer.gsi.state is None
    cs2 = MatrixService(
        obs_dry_run=True,
        config_path=str(tmp_path / "cs2.json"),
        sport_mode="cs2",
    )
    try:
        cs2.feed_soccer_events({"kind": "heartbeat", "match_id": "x", "timestamp_ms": 1, "sequence": 1})
        assert False, "cs2 mode must not accept soccer events"
    except RuntimeError:
        pass


def test_runtime_sport_switch(tmp_path):
    service = MatrixService(
        obs_dry_run=True,
        config_path=str(tmp_path / "rt.json"),
        sport_mode="cs2",
    )
    assert service.soccer is None
    result = service.set_sport_mode("soccer")
    assert result["changed"] is True
    assert service.soccer is not None
    service.feed_soccer_events(heartbeat(sequence=1))
    reloaded = MatrixService(
        obs_dry_run=True, config_path=str(tmp_path / "rt.json")
    )
    assert reloaded.sport_mode == "soccer"
    back = service.set_sport_mode("cs2")
    assert back["changed"] is True
    assert service.soccer is None
    assert service.set_sport_mode("cs2")["changed"] is False


def test_camera_loss_falls_back_to_main(tmp_path):
    set_now_override(3_000)
    service = MatrixService(
        obs_dry_run=True,
        config_path=str(tmp_path / "rt.json"),
        sport_mode="soccer",
    )
    from excalibur.matrix.domain import SlotState
    from excalibur.matrix.soccer.domain import CameraAssignment

    assignment = CameraAssignment(
        input_id="input_02",
        role=CameraRole.GOAL_LINE.value,
        confidence=0.95,
        source="vision",
        visible_ball=True,
        sharpness=0.8,
        coverage=0.4,
    )
    assert service.soccer_cameras is not None
    service.soccer_cameras.update(assignment)
    service.soccer_fusion.cameras["input_02"] = assignment  # type: ignore[union-attr]
    service.feed_soccer_events(event("penalty_kick", sequence=1, phase="execution"))
    advance_now_override(400)
    service.soccer_director.tick()  # type: ignore[union-attr]
    service.matrix.configure_input("input_02", state=SlotState.MISSING_SIGNAL)
    advance_now_override(2_000)
    service.soccer_director.tick()  # type: ignore[union-attr]
    assert service.matrix.current_input_for("output_01") in {None, "input_01"}
    set_now_override(None)
