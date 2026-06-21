from __future__ import annotations

import pytest

from obs_auto_director.matrix.clock import set_now_override
from obs_auto_director.matrix.domain import ControlAction, ControlCommand
from obs_auto_director.matrix.media.output import (
    DEFAULT_PROGRAM_PUBLISH_URL,
    ProgramOutputManager,
    build_program_command,
    program_obs_url,
)
from obs_auto_director.matrix.service import MatrixService


@pytest.fixture(autouse=True)
def reset_clock():
    yield
    set_now_override(None)


def test_build_program_command_copies_source_by_default():
    cmd = build_program_command(
        "srt://127.0.0.1:8554?streamid=read:input_01",
        DEFAULT_PROGRAM_PUBLISH_URL,
    )
    assert cmd[0] == "ffmpeg"
    assert "-c" in cmd and "copy" in cmd
    assert cmd[-3:] == ["-f", "mpegts", DEFAULT_PROGRAM_PUBLISH_URL]


def test_build_program_command_can_transcode():
    cmd = build_program_command("rtsp://127.0.0.1:8554/input_01", "srt://x:1", "libx264")
    assert "-rtsp_transport" in cmd  # rtsp gets tcp transport
    assert "libx264" in cmd
    assert "copy" not in cmd


def test_program_obs_url_converts_listener_to_caller():
    obs_url = program_obs_url(DEFAULT_PROGRAM_PUBLISH_URL, "10.0.0.5")
    assert obs_url == "srt://10.0.0.5:9999?mode=caller&latency=200"


def test_program_obs_url_leaves_non_listener_untouched():
    publish = "srt://127.0.0.1:8890?streamid=publish:program"
    assert program_obs_url(publish, "10.0.0.5") == publish


def test_inactive_manager_tracks_source_without_spawning_ffmpeg():
    # No media enabled -> no FFmpeg should be started, but the intended source
    # is still tracked so the UI can report it.
    manager = ProgramOutputManager()
    manager.set_source("input_01", "srt://127.0.0.1:9001?mode=caller")
    assert manager.current_input == "input_01"
    assert manager.current_read_url == "srt://127.0.0.1:9001?mode=caller"
    assert manager.is_running() is False
    assert manager.active is False


def test_program_route_updates_program_output_source():
    service = MatrixService(obs_dry_run=True)
    command = ControlCommand(
        action=ControlAction.MANUAL_CUT,
        operator="test",
        target_output=service.program_output,
        candidate_input="input_01",
        reason="test_take",
    )
    decision = service.routing.handle_control(command)

    assert decision.outcome.value == "accepted"
    # The Excalibur-owned program feed now follows the routed input...
    assert service.program.current_input == "input_01"
    assert service.program.current_read_url == service.matrix.inputs["input_01"].uri
    # ...without spawning FFmpeg while running in simulate mode (no real media).
    assert service.program.is_running() is False


def test_program_output_url_is_exposed_and_separate_from_preview():
    service = MatrixService(obs_dry_run=True)
    config = service.config_snapshot()

    assert config["program"]["publish_url"] == service.program.publish_url
    assert "mode=caller" in config["program"]["obs_url"]

    program_slot = next(
        o for o in config["outputs"] if o["id"] == service.program_output
    )
    assert program_slot["uri"] == service.program.publish_url

    # The agent preview path is independent: no media means no preview frame,
    # even though the program output URL exists.
    assert service.preview_jpeg("input_01") is None
