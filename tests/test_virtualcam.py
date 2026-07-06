from __future__ import annotations

import pytest

from excalibur.matrix.clock import set_now_override
from excalibur.matrix.domain import ControlAction, ControlCommand
from excalibur.matrix.media.virtualcam import (
    DEFAULT_DEVICE_NAME,
    VirtualCamOutputManager,
    build_decode_command,
    build_vcam_stream_command,
    vcam_setup_commands,
)
from excalibur.matrix.service import MatrixService


@pytest.fixture(autouse=True)
def reset_clock():
    yield
    set_now_override(None)


def test_build_decode_command_emits_rawvideo_rgb24():
    cmd = build_decode_command(
        "srt://127.0.0.1:9001?mode=caller", 1920, 1080, 30
    )
    assert cmd[0] == "ffmpeg"
    assert "-an" in cmd  # video-only device
    assert "rawvideo" in cmd
    assert "rgb24" in cmd
    # scale/fps baked into the filter graph so the device sees a fixed geometry.
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=1920:1080" in vf
    assert "fps=30" in vf
    assert cmd[-1] == "pipe:1"


def test_build_decode_command_handles_file_source():
    cmd = build_decode_command(
        "C:/streams/demo.mp4", 1280, 720, 30, source_type="file", loop=True
    )
    assert cmd.index("-stream_loop") < cmd.index("-i")
    assert "-re" in cmd


def test_build_decode_command_uses_tcp_for_rtsp():
    cmd = build_decode_command("rtsp://10.0.0.5/cam", 1920, 1080, 30, source_type="rtsp")
    assert "-rtsp_transport" in cmd


def test_build_vcam_stream_command_targets_device():
    cmd = build_vcam_stream_command("AkVCamManager", "Excalibur", 1920, 1080, 30)
    assert cmd[0] == "AkVCamManager"
    assert "stream" in cmd
    assert "Excalibur" in cmd
    assert "RGB24" in cmd
    assert "1920" in cmd and "1080" in cmd


def test_setup_commands_register_named_device():
    cmds = vcam_setup_commands("Excalibur", 1920, 1080, 30)
    joined = "\n".join(cmds)
    assert 'add-device "Excalibur" --id Excalibur' in joined
    assert "add-format Excalibur RGB24 1920 1080 30" in joined
    assert "update" in joined


def test_inactive_manager_tracks_source_without_spawning():
    manager = VirtualCamOutputManager()
    manager.set_source("input_01", "srt://127.0.0.1:9001?mode=caller")
    assert manager.current_input == "input_01"
    assert manager.current_read_url == "srt://127.0.0.1:9001?mode=caller"
    assert manager.is_running() is False
    assert manager.active is False
    assert manager.device_name == DEFAULT_DEVICE_NAME


def test_virtualcam_off_by_default_and_in_snapshot(tmp_path):
    service = MatrixService(
        obs_dry_run=True, config_path=str(tmp_path / "runtime.json")
    )
    config = service.config_snapshot()
    vcam = config["virtualcam"]
    assert vcam["enabled"] is False
    assert vcam["device_name"] == "Excalibur"
    # Setup commands are surfaced so the operator can register the device.
    assert any("add-device" in c for c in vcam["setup_commands"])


def test_program_route_also_updates_virtualcam_source(tmp_path):
    service = MatrixService(
        obs_dry_run=True, config_path=str(tmp_path / "runtime.json")
    )
    command = ControlCommand(
        action=ControlAction.MANUAL_CUT,
        operator="test",
        target_output=service.program_output,
        candidate_input="input_01",
        reason="test_take",
    )
    service.routing.handle_control(command)
    # The virtual camera follows the same routed input as the SRT program path,
    # without spawning processes in simulate mode (no real media).
    assert service.virtualcam.current_input == service.program.current_input
    assert service.virtualcam.current_read_url == service.program.current_read_url
    assert service.virtualcam.is_running() is False


def test_set_virtualcam_config_persists_and_reports(tmp_path):
    service = MatrixService(
        obs_dry_run=True, config_path=str(tmp_path / "runtime.json")
    )
    status = service.set_virtualcam_config(
        {"enabled": True, "device": "Excalibur", "width": 1280, "height": 720, "fps": 60}
    )
    assert service.virtualcam_enabled is True
    assert status["device_name"] == "Excalibur"
    assert status["width"] == 1280
    assert status["height"] == 720
    assert status["fps"] == 60
    # Reloading the persisted config reflects the new settings.
    assert service.config_store.virtualcam["enabled"] is True
    assert service.config_store.virtualcam["fps"] == 60
