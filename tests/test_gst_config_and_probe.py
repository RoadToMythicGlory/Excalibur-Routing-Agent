from __future__ import annotations

import json

import pytest

from excalibur.matrix.config_store import (
    BACKEND_FFMPEG_COMPOSITOR,
    BACKEND_GSTREAMER,
    MatrixConfigStore,
)
from excalibur.matrix.media import gst_probe
from excalibur.matrix.media.compositor import CompositorOutputManager
from excalibur.matrix.media.gstreamer_output import GstProgramOutputManager
from excalibur.matrix.service import MatrixService


# -- config store ------------------------------------------------------------
def test_backend_defaults_to_ffmpeg_compositor(tmp_path):
    store = MatrixConfigStore(str(tmp_path / "runtime.json"))
    assert store.program_output_backend == BACKEND_FFMPEG_COMPOSITOR
    settings = store.program_output_settings
    assert settings["width"] == 1920
    assert settings["height"] == 1080
    assert settings["fps"] == 30
    assert settings["encoder"] == "x264enc"


def test_backend_setter_rejects_unknown_value(tmp_path):
    store = MatrixConfigStore(str(tmp_path / "runtime.json"))
    assert store.set_program_output_backend("gstreamer") == BACKEND_GSTREAMER
    # Unknown backend falls back to the safe default.
    assert store.set_program_output_backend("nonsense") == BACKEND_FFMPEG_COMPOSITOR


def test_program_output_settings_persist_and_coerce(tmp_path):
    path = tmp_path / "runtime.json"
    store = MatrixConfigStore(str(path))
    store.set_program_output_settings(
        {"width": "1280", "height": "720", "fps": "60", "encoder": " nvh264enc "}
    )
    reloaded = MatrixConfigStore(str(path))
    settings = reloaded.program_output_settings
    assert settings["width"] == 1280
    assert settings["height"] == 720
    assert settings["fps"] == 60
    assert settings["encoder"] == "nvh264enc"


def test_backend_persists_across_reload(tmp_path):
    path = tmp_path / "runtime.json"
    MatrixConfigStore(str(path)).set_program_output_backend(BACKEND_GSTREAMER)
    assert MatrixConfigStore(str(path)).program_output_backend == BACKEND_GSTREAMER


# -- service backend selection -----------------------------------------------
def _write_runtime(path, backend):
    path.write_text(
        json.dumps({"program_output_backend": backend}), encoding="utf-8"
    )


def test_service_defaults_to_compositor(tmp_path):
    service = MatrixService(obs_dry_run=True, config_path=str(tmp_path / "runtime.json"))
    assert isinstance(service.program, CompositorOutputManager)


def test_service_selects_gstreamer_backend(tmp_path):
    runtime = tmp_path / "runtime.json"
    _write_runtime(runtime, "gstreamer")
    service = MatrixService(obs_dry_run=True, config_path=str(runtime))
    assert isinstance(service.program, GstProgramOutputManager)
    # The program output URL is still exposed identically to OBS.
    assert "mode=caller" in service.program_obs_url()


def test_gstreamer_program_status_shape(tmp_path):
    runtime = tmp_path / "runtime.json"
    _write_runtime(runtime, "gstreamer")
    service = MatrixService(obs_dry_run=True, config_path=str(runtime))
    snapshot = service.config_snapshot()
    assert snapshot["program"]["backend"] == "gstreamer"
    # Not enabled (no media) -> idle and not running, but never raises.
    assert service.program.is_running() is False


# -- gst probe ---------------------------------------------------------------
def test_probe_reports_missing_when_gstreamer_absent(monkeypatch):
    gst_probe.reset_cache()
    monkeypatch.setattr(gst_probe.shutil, "which", lambda _name: None)
    assert gst_probe.gstreamer_available() is False
    assert gst_probe.element_available("srtsink") is False
    env = gst_probe.describe_environment()
    assert env["runtime_available"] is False
    assert env["ready"] is False
    # Every required element is reported missing when there is no runtime.
    assert set(env["missing_required"]) == set(gst_probe.REQUIRED_ELEMENTS)
    gst_probe.reset_cache()


def test_probe_caches_element_results(monkeypatch):
    gst_probe.reset_cache()
    monkeypatch.setattr(gst_probe.shutil, "which", lambda _name: "/usr/bin/gst-inspect-1.0")

    calls = {"n": 0}

    class _Result:
        returncode = 0

    def _run(*_args, **_kwargs):
        calls["n"] += 1
        return _Result()

    monkeypatch.setattr(gst_probe.subprocess, "run", _run)
    assert gst_probe.element_available("appsrc") is True
    assert gst_probe.element_available("appsrc") is True  # cached
    assert calls["n"] == 1
    gst_probe.reset_cache()
