from __future__ import annotations

import os

from obs_auto_director.matrix.clock import set_now_override
from obs_auto_director.matrix.matrix_core import MatrixCore
from obs_auto_director.matrix.media.ingest import (
    MediaIngestManager,
    MediaInput,
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
