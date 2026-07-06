from __future__ import annotations

import json
import queue
import time

import pytest

from excalibur.matrix.clock import set_now_override
from excalibur.matrix.media import gstreamer_output as gst_mod
from excalibur.matrix.media import media_core_protocol as proto
from excalibur.matrix.media.gstreamer_output import (
    GstProgramOutputManager,
    _to_media_core_input,
)
from excalibur.matrix.media.ingest import MediaInput


@pytest.fixture(autouse=True)
def reset_clock():
    yield
    set_now_override(None)


# -- protocol ----------------------------------------------------------------
def test_build_start_payload_mirrors_config():
    cfg = proto.OutputConfig(width=1280, height=720, fps=60, publish_url="srt://x:1")
    payload = proto.build_start(cfg)
    assert payload["cmd"] == proto.CMD_START
    assert payload["width"] == 1280
    assert payload["height"] == 720
    assert payload["fps"] == 60
    assert payload["publish_url"] == "srt://x:1"


def test_build_take_maps_none_to_empty_string():
    assert proto.build_take(None)["input_id"] == ""
    assert proto.build_take("input_03")["input_id"] == "input_03"


def test_encode_command_is_single_json_line():
    line = proto.encode_command(proto.build_status())
    assert line.endswith("\n")
    assert line.count("\n") == 1
    assert json.loads(line)["cmd"] == proto.CMD_STATUS


def test_parse_status_reads_all_fields():
    message = {
        "type": "status",
        "state": "running",
        "active_input": "input_02",
        "negotiated_caps": "video/x-raw",
        "encoder": "x264enc",
        "output_url": "srt://x:1",
        "fps": 29.97,
        "pushed_frames": 100,
        "dropped_frames": 2,
        "black_frames": 5,
        "last_error": None,
    }
    status = proto.parse_status(message)
    assert status.running is True
    assert status.active_input == "input_02"
    assert status.pushed_frames == 100
    assert status.black_frames == 5


def test_parse_message_rejects_non_json():
    assert proto.parse_message("not json") is None
    assert proto.parse_message("") is None
    assert proto.parse_message("[1,2,3]") is None  # not an object


# -- input conversion --------------------------------------------------------
def test_file_input_becomes_file_uri(tmp_path):
    media_file = tmp_path / "clip.mp4"
    media_file.write_bytes(b"x")
    spec = MediaInput(
        input_id="input_04", read_url=str(media_file), source_type="file", loop=True
    )
    converted = _to_media_core_input(spec)
    assert converted.url.startswith("file://")
    assert converted.source_type == "file"
    assert converted.loop is True


def test_srt_input_url_is_passed_through():
    spec = MediaInput(
        input_id="input_01",
        read_url="srt://0.0.0.0:9001?mode=listener",
        source_type="srt",
    )
    converted = _to_media_core_input(spec)
    assert converted.url == "srt://0.0.0.0:9001?mode=listener"


# -- manager lifecycle (media-core mocked) -----------------------------------
class _FakeStdin:
    def __init__(self):
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        self.lines.append(text)

    def flush(self) -> None:
        pass


class _FakeStdout:
    """Blocking, queue-backed stdout so the reader thread behaves like a pipe."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()

    def push(self, line: str) -> None:
        self._q.put(line if line.endswith("\n") else line + "\n")

    def close_stream(self) -> None:
        self._q.put(None)

    def __iter__(self):
        return self

    def __next__(self):
        item = self._q.get()
        if item is None:
            raise StopIteration
        return item


class _FakeProc:
    def __init__(self, *_args, **_kwargs):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout()
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False
        self.stdout.close_stream()

    def wait(self, timeout=None):
        return 0

    def kill(self):  # pragma: no cover - defensive
        self._alive = False
        self.stdout.close_stream()


@pytest.fixture
def fake_media_core(monkeypatch, tmp_path):
    """media-core 'found' and spawnable, returning controllable fake processes."""
    started: list[_FakeProc] = []

    def _popen(*args, **kwargs):
        proc = _FakeProc(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(gst_mod, "find_media_core", lambda: "media-core")
    monkeypatch.setattr(gst_mod.subprocess, "Popen", _popen)
    return started


def _sent_commands(proc: _FakeProc) -> list[dict]:
    return [json.loads(line) for line in proc.stdin.lines]


def _make_manager(tmp_path, **kwargs) -> GstProgramOutputManager:
    kwargs.setdefault("log_path", str(tmp_path / "media_core.log"))
    return GstProgramOutputManager("srt://0.0.0.0:9999?mode=listener", **kwargs)


def _specs(n: int) -> list[MediaInput]:
    return [
        MediaInput(
            input_id=f"input_0{i + 1}",
            read_url=f"rtsp://127.0.0.1:8554/input_0{i + 1}",
            source_type="rtsp",
        )
        for i in range(n)
    ]


def test_enable_spawns_and_sends_start_inputs_take(fake_media_core, tmp_path):
    manager = _make_manager(tmp_path)
    manager.set_inputs(_specs(2))
    manager.set_active("input_01", "rtsp://127.0.0.1:8554/input_01")

    manager.enable()
    try:
        assert len(fake_media_core) == 1
        cmds = _sent_commands(fake_media_core[0])
        kinds = [c["cmd"] for c in cmds]
        # Spawn brings the process up to the desired state in order.
        assert kinds[0] == proto.CMD_START
        assert proto.CMD_SET_INPUTS in kinds
        assert proto.CMD_TAKE in kinds
        take = next(c for c in cmds if c["cmd"] == proto.CMD_TAKE)
        assert take["input_id"] == "input_01"
        set_inputs = next(c for c in cmds if c["cmd"] == proto.CMD_SET_INPUTS)
        assert [i["id"] for i in set_inputs["inputs"]] == ["input_01", "input_02"]
    finally:
        manager.stop()


def test_set_active_sends_take_when_running(fake_media_core, tmp_path):
    manager = _make_manager(tmp_path)
    manager.set_inputs(_specs(2))
    manager.enable()
    try:
        fake_media_core[0].stdin.lines.clear()
        manager.set_active("input_02", "rtsp://127.0.0.1:8554/input_02")
        cmds = _sent_commands(fake_media_core[0])
        assert any(
            c["cmd"] == proto.CMD_TAKE and c["input_id"] == "input_02" for c in cmds
        )
        assert manager.current_input == "input_02"
    finally:
        manager.stop()


def test_set_active_none_sends_black_take(fake_media_core, tmp_path):
    manager = _make_manager(tmp_path)
    manager.set_inputs(_specs(1))
    manager.enable()
    try:
        fake_media_core[0].stdin.lines.clear()
        manager.set_active(None, None)  # nothing routed -> hold black
        cmds = _sent_commands(fake_media_core[0])
        assert any(
            c["cmd"] == proto.CMD_TAKE and c["input_id"] == "" for c in cmds
        )
    finally:
        manager.stop()


def test_membership_change_sends_set_inputs(fake_media_core, tmp_path):
    manager = _make_manager(tmp_path)
    manager.set_inputs(_specs(1))
    manager.enable()
    try:
        fake_media_core[0].stdin.lines.clear()
        # Same membership -> no new command.
        manager.set_inputs(_specs(1))
        assert _sent_commands(fake_media_core[0]) == []
        # Added input -> exactly one set_inputs.
        manager.set_inputs(_specs(2))
        cmds = _sent_commands(fake_media_core[0])
        set_inputs = [c for c in cmds if c["cmd"] == proto.CMD_SET_INPUTS]
        assert len(set_inputs) == 1
        assert len(set_inputs[0]["inputs"]) == 2
    finally:
        manager.stop()


def test_status_mirrors_media_core_frames(fake_media_core, tmp_path):
    manager = _make_manager(tmp_path)
    manager.set_inputs(_specs(1))
    manager.enable()
    try:
        proc = fake_media_core[0]
        proc.stdout.push(
            json.dumps(
                {
                    "type": "status",
                    "state": "running",
                    "active_input": "input_01",
                    "negotiated_caps": "video/x-raw",
                    "encoder": "x264enc",
                    "output_url": "srt://0.0.0.0:9999?mode=listener",
                    "fps": 30.0,
                    "pushed_frames": 90,
                    "dropped_frames": 0,
                    "black_frames": 3,
                    "last_error": None,
                }
            )
        )
        # Give the reader thread a moment to consume the frame.
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if manager.status()["pushed_frames"] == 90:
                break
            time.sleep(0.02)
        status = manager.status()
        assert status["backend"] == "gstreamer"
        assert status["state"] == "running"
        assert status["pushed_frames"] == 90
        assert status["black_frames"] == 3
    finally:
        manager.stop()


def test_error_frame_is_captured(fake_media_core, tmp_path):
    manager = _make_manager(tmp_path)
    manager.set_inputs(_specs(1))
    manager.enable()
    try:
        proc = fake_media_core[0]
        proc.stdout.push(
            json.dumps(
                {
                    "type": "error",
                    "message": "caps negotiation failed",
                    "source_url": "rtsp://h/1",
                    "element": "videoscale",
                    "requested_caps": "video/x-raw",
                }
            )
        )
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if manager.status()["last_error"]:
                break
            time.sleep(0.02)
        assert manager.status()["last_error"] == "caps negotiation failed"
    finally:
        manager.stop()


def test_stop_sends_stop_command(fake_media_core, tmp_path):
    manager = _make_manager(tmp_path)
    manager.enable()
    proc = fake_media_core[0]
    manager.stop()
    cmds = _sent_commands(proc)
    assert any(c["cmd"] == proto.CMD_STOP for c in cmds)
    assert manager.is_running() is False


def test_missing_binary_degrades_gracefully(monkeypatch, tmp_path):
    monkeypatch.setattr(gst_mod, "find_media_core", lambda: None)
    manager = _make_manager(tmp_path)
    manager.set_inputs(_specs(1))
    manager.set_active("input_01", "rtsp://h/1")
    # Must not raise even though no binary exists.
    manager.enable()
    try:
        assert manager.is_running() is False
        status = manager.status()
        assert status["running"] is False
        assert status["active"] is True  # still tracks intended state
    finally:
        manager.stop()


def test_set_active_tracks_source_without_running(tmp_path):
    manager = _make_manager(tmp_path)
    manager.set_active("input_01", "rtsp://h/1")
    assert manager.current_input == "input_01"
    assert manager.current_read_url == "rtsp://h/1"
    assert manager.is_running() is False
    assert manager.active is False
