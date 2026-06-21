from __future__ import annotations

import os

from pathlib import Path

from obs_auto_director.matrix.clock import set_now_override
from obs_auto_director.matrix.matrix_core import MatrixCore
from obs_auto_director.matrix.media.ingest import (
    MediaIngestManager,
    MediaInput,
    build_ingest_command,
    build_input_args,
    build_srt_url,
)


def test_listener_url_binds_local_port():
    url = build_srt_url("listener", None, 9001, 200)
    assert url == "srt://0.0.0.0:9001?mode=listener&latency=200"


def test_caller_url_dials_host_and_port():
    url = build_srt_url("caller", "10.0.0.5", 1234, 120)
    assert url == "srt://10.0.0.5:1234?mode=caller&latency=120"


def test_caller_without_host_defaults_to_loopback():
    url = build_srt_url("caller", None, 5000)
    assert url == "srt://127.0.0.1:5000?mode=caller&latency=200"


def test_srt_input_args_use_low_buffering_not_re():
    spec = MediaInput("input_01", "srt://0.0.0.0:9001?mode=listener", source_type="srt")
    args = build_input_args(spec)
    assert "-re" not in args
    assert "-fflags" in args and "nobuffer" in args
    assert args[-2:] == ["-i", spec.read_url]


def test_rtsp_input_args_force_tcp_transport():
    spec = MediaInput("input_01", "rtsp://127.0.0.1:8554/input_01", source_type="rtsp")
    args = build_input_args(spec)
    assert "-rtsp_transport" in args and "tcp" in args
    assert "-re" not in args


def test_file_input_args_play_realtime_without_loop_by_default():
    spec = MediaInput("input_05", "C:/streams/demo.mp4", source_type="file")
    args = build_input_args(spec)
    assert "-re" in args
    assert "-stream_loop" not in args
    # -re must precede -i so playback is real time.
    assert args.index("-re") < args.index("-i")


def test_file_input_args_loop_forever_when_enabled():
    spec = MediaInput("input_05", "C:/streams/demo.mp4", source_type="file", loop=True)
    args = build_input_args(spec)
    assert args[:2] == ["-stream_loop", "-1"]
    assert "-re" in args
    # loop flag is an input option and must precede -i.
    assert args.index("-stream_loop") < args.index("-i")


def test_build_ingest_command_writes_preview_jpeg():
    spec = MediaInput("input_05", "C:/streams/demo.mp4", source_type="file", loop=True)
    cmd = build_ingest_command(spec, Path(".preview/input_05.jpg"))
    assert cmd[0] == "ffmpeg"
    assert "-stream_loop" in cmd and "-re" in cmd
    assert "-update" in cmd and "1" in cmd
    assert cmd[-1].endswith("input_05.jpg")


def test_stale_preview_is_not_served(tmp_path):
    preview = tmp_path / "input_01.jpg"
    preview.write_bytes(b"old-frame")
    os.utime(preview, (1, 1))

    set_now_override(10_000)
    try:
        manager = MediaIngestManager(
            MatrixCore(),
            [MediaInput("input_01", "srt://0.0.0.0:9001?mode=listener")],
            preview_dir=tmp_path,
            freshness_ms=1000,
        )

        assert manager.latest_jpeg("input_01") is None
        assert not preview.exists()
    finally:
        set_now_override(None)
