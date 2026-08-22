from __future__ import annotations

from excalibur.matrix.clock import advance_now_override, now_ms, set_now_override
from excalibur.matrix.service import MatrixService
from excalibur.matrix.soccer.simulator import (
    burst_attack_sequence,
    corner_sequence,
    foul_to_free_kick,
    penalty_sequence,
)


def _play(tmp_path, sequence: list[dict]) -> MatrixService:
    set_now_override(8_000)
    service = MatrixService(
        obs_dry_run=True,
        config_path=str(tmp_path / "runtime.json"),
        sport_mode="soccer",
    )
    for payload in sequence:
        payload["timestamp_ms"] = now_ms()
        service.feed_soccer_events(payload)
        advance_now_override(200)
        if service.soccer_director is not None:
            service.soccer_director.tick()
    return service


def test_vod_like_penalty_replay_returns_main(tmp_path):
    service = _play(tmp_path, penalty_sequence())
    advance_now_override(1_200)
    service.soccer_director.tick()  # type: ignore[union-attr]
    current = service.matrix.current_input_for("output_01")
    assert current in {None, "input_01"}
    assert service.state_snapshot()["sport_mode"] == "soccer"
    assert service.state_snapshot()["soccer"]["profile_version"] == "1.0.0"
    set_now_override(None)


def test_each_special_event_sequence_is_accepted(tmp_path):
    for sequence in (
        foul_to_free_kick(),
        penalty_sequence(),
        corner_sequence(),
        burst_attack_sequence(),
    ):
        service = _play(tmp_path, sequence)
        assert service.soccer is not None
        assert service.soccer.rejected == 0
    set_now_override(None)
