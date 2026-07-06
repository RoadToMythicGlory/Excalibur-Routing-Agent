from __future__ import annotations

import pytest

from excalibur.matrix.clock import set_now_override
from excalibur.matrix.media import compositor as comp_mod
from excalibur.matrix.media.compositor import (
    CompositorOutputManager,
    build_compositor_command,
)
from excalibur.matrix.media.ingest import MediaInput


@pytest.fixture(autouse=True)
def reset_clock():
    yield
    set_now_override(None)


def _specs(n: int) -> list[MediaInput]:
    return [
        MediaInput(input_id=f"input_0{i + 1}", read_url=f"srt://h:{9000 + i}", source_type="srt")
        for i in range(n)
    ]


class _FakeProc:
    """Minimal stand-in for a running FFmpeg subprocess."""

    def __init__(self, *_args, **_kwargs):
        self.terminated = False

    def poll(self):  # None == still running
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):  # pragma: no cover - defensive
        pass


@pytest.fixture
def patched_ffmpeg(monkeypatch):
    """Make spawning deterministic: ffmpeg present, Popen counted, no real process."""
    started: list[_FakeProc] = []

    def _popen(*args, **kwargs):
        proc = _FakeProc(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(comp_mod, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(comp_mod.subprocess, "Popen", _popen)
    return started


# -- command construction ----------------------------------------------------
def test_build_compositor_command_structure():
    cmd = build_compositor_command(
        _specs(2), "srt://0.0.0.0:9999?mode=listener", active_index=1
    )
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg"
    # 2 real inputs + black slate -> streamselect over 3 pads, baked active index.
    # Named filters so the zmq broker can target them at runtime.
    assert "streamselect@progv=inputs=3:map=1" in joined
    assert "astreamselect@proga=inputs=3:map=1" in joined
    # Seamless-switch hook and NVDEC/NVENC path.
    assert "zmq=bind_address=" in joined
    assert "h264_nvenc" in cmd
    assert cmd.count("-hwaccel") == 2  # one NVDEC decode per real input
    # One continuous mpegts program output.
    assert cmd[-3:] == ["-f", "mpegts", "srt://0.0.0.0:9999?mode=listener"]
    assert "[vout]" in cmd and "[aout]" in cmd


def test_build_compositor_command_without_audio():
    cmd = build_compositor_command(_specs(1), "srt://x:1", include_audio=False)
    joined = " ".join(cmd)
    assert "astreamselect" not in joined
    assert "anullsrc" not in joined
    assert "[aout]" not in cmd


def test_build_compositor_command_can_disable_hwaccel():
    cmd = build_compositor_command(_specs(2), "srt://x:1", hwaccel=None)
    assert "-hwaccel" not in cmd


# -- index mapping / black-slate fallback ------------------------------------
def test_index_for_maps_inputs_then_black_slate():
    manager = CompositorOutputManager()
    manager.set_inputs(_specs(2))  # not enabled -> just tracked, no spawn
    assert manager._index_for("input_01") == 0
    assert manager._index_for("input_02") == 1
    # Unknown input or nothing routed falls back to the black slate (index == n).
    assert manager._index_for(None) == 2
    assert manager._index_for("input_99") == 2
    assert manager.is_running() is False


# -- seamless switching vs respawn -------------------------------------------
def test_cut_is_a_seamless_switch_not_a_respawn(patched_ffmpeg, monkeypatch):
    started = patched_ffmpeg
    manager = CompositorOutputManager()
    sent: list[str] = []
    monkeypatch.setattr(manager._switch, "send", lambda cmd, **_k: sent.append(cmd) or True)

    manager.active = True
    manager.set_inputs(_specs(2))  # spawns the persistent process once
    assert len(started) == 1

    manager.set_active("input_02", "srt://h:9001")  # ZMQ switch, no respawn
    assert len(started) == 1
    assert "streamselect@progv map 1" in sent  # video select switched to pad 1
    assert "astreamselect@proga map 1" in sent  # audio select switched in lockstep
    assert manager.current_input == "input_02"


def test_membership_change_rebuilds_graph(patched_ffmpeg):
    started = patched_ffmpeg
    manager = CompositorOutputManager()
    manager.active = True

    manager.set_inputs(_specs(2))
    assert len(started) == 1
    # Same membership -> no rebuild.
    manager.set_inputs(_specs(2))
    assert len(started) == 1
    # Adding an input -> exactly one respawn.
    manager.set_inputs(_specs(3))
    assert len(started) == 2


def test_set_active_tracks_source_without_running():
    manager = CompositorOutputManager()
    manager.set_active("input_01", "srt://127.0.0.1:9001?mode=caller")
    assert manager.current_input == "input_01"
    assert manager.current_read_url == "srt://127.0.0.1:9001?mode=caller"
    assert manager.is_running() is False
    assert manager.active is False


def test_status_reports_membership_and_publish_url(patched_ffmpeg):
    manager = CompositorOutputManager("srt://0.0.0.0:9999?mode=listener")
    manager.active = True
    manager.set_inputs(_specs(2))
    status = manager.status()
    assert status["publish_url"] == "srt://0.0.0.0:9999?mode=listener"
    assert status["inputs"] == ["input_01", "input_02"]
    assert status["running"] is True
