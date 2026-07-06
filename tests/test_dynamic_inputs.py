from __future__ import annotations

import json

import pytest

from excalibur.matrix.clock import set_now_override
from excalibur.matrix.config_store import MatrixConfigStore
from excalibur.matrix.domain import ControlAction, ControlCommand
from excalibur.matrix.service import MatrixService, browse_filesystem


@pytest.fixture(autouse=True)
def reset_clock():
    set_now_override(1_000.0)
    yield
    set_now_override(None)


def _service(tmp_path, **kwargs) -> MatrixService:
    config_path = tmp_path / "matrix.runtime.json"
    return MatrixService(obs_dry_run=True, config_path=str(config_path), **kwargs)


# -- persisted counts --------------------------------------------------------
def test_missing_counts_default_to_eleven(tmp_path):
    # An older runtime file without counts must still load as 11x11.
    path = tmp_path / "matrix.runtime.json"
    path.write_text(json.dumps({"program_output": "output_01", "inputs": {}}), "utf-8")
    service = MatrixService(obs_dry_run=True, config_path=str(path))
    assert len(service.matrix.inputs) == 11
    assert len(service.matrix.outputs) == 11


def test_persisted_counts_create_expected_slots(tmp_path):
    path = tmp_path / "matrix.runtime.json"
    path.write_text(
        json.dumps(
            {
                "program_output": "output_01",
                "input_count": 4,
                "output_count": 6,
                "inputs": {},
            }
        ),
        "utf-8",
    )
    service = MatrixService(obs_dry_run=True, config_path=str(path))
    assert len(service.matrix.inputs) == 4
    assert len(service.matrix.outputs) == 6
    assert "input_04" in service.matrix.inputs
    assert "input_05" not in service.matrix.inputs


def test_config_store_coerces_invalid_counts(tmp_path):
    path = tmp_path / "matrix.runtime.json"
    path.write_text(json.dumps({"input_count": "abc", "output_count": 0}), "utf-8")
    store = MatrixConfigStore(str(path))
    assert store.input_count == 11
    assert store.output_count == 11


# -- runtime resize ----------------------------------------------------------
def test_set_matrix_size_grows_and_persists(tmp_path):
    service = _service(tmp_path, input_count=3, output_count=3)
    result = service.set_matrix_size(input_count=5, output_count=4)
    assert result == {"input_count": 5, "output_count": 4}
    assert "input_05" in service.matrix.inputs
    assert "output_04" in service.matrix.outputs
    # Persisted for next start.
    assert service.config_store.input_count == 5
    assert service.config_store.output_count == 4


def test_set_matrix_size_shrinks_trailing_slot(tmp_path):
    service = _service(tmp_path, input_count=3, output_count=3)
    service.set_matrix_size(input_count=2)
    assert "input_03" not in service.matrix.inputs
    assert service.config_store.input_count == 2


def test_cannot_remove_routed_input(tmp_path):
    service = _service(tmp_path, input_count=3, output_count=3)
    command = ControlCommand(
        action=ControlAction.MANUAL_CUT,
        operator="test",
        target_output=service.program_output,
        candidate_input="input_03",
        reason="take",
    )
    assert service.routing.handle_control(command).outcome.value == "accepted"
    with pytest.raises(ValueError):
        service.set_matrix_size(input_count=2)


def test_output_count_must_be_at_least_one(tmp_path):
    service = _service(tmp_path, input_count=3, output_count=3)
    with pytest.raises(ValueError):
        service.set_matrix_size(output_count=0)


def test_cannot_remove_program_output_slot(tmp_path):
    service = _service(tmp_path, input_count=3, output_count=3)
    # Make the highest-index output the program output, then try to remove it.
    service.program_output = "output_03"
    with pytest.raises(ValueError):
        service.set_matrix_size(output_count=2)


# -- removed / unknown slots are not routable --------------------------------
def test_removed_slot_cannot_be_routed(tmp_path):
    service = _service(tmp_path, input_count=3, output_count=3)
    service.set_matrix_size(input_count=2)
    command = ControlCommand(
        action=ControlAction.MANUAL_CUT,
        operator="test",
        target_output=service.program_output,
        candidate_input="input_03",
        reason="take",
    )
    decision = service.routing.handle_control(command)
    assert decision.outcome.value == "rejected"


def test_unknown_input_config_is_rejected(tmp_path):
    service = _service(tmp_path, input_count=3, output_count=3)
    with pytest.raises(KeyError):
        service.update_input_config("input_99", {"player_name": "ghost"})


# -- source-mode resolution + program metadata -------------------------------
def test_source_mode_maps_legacy_srt_mode(tmp_path):
    service = _service(tmp_path, input_count=3, output_count=3)
    service.config_store.set_input("input_02", {"srt_mode": "listener"})
    assert service._resolve_source_mode(service.config_store.input_config("input_02")) == (
        "srt_listener"
    )
    assert service._source_meta("input_02") == ("srt", False)


def test_file_source_meta_reports_loop(tmp_path):
    service = _service(tmp_path, input_count=3, output_count=3)
    service.config_store.set_input(
        "input_02", {"source_mode": "file", "file_path": "demo.mp4", "loop": True}
    )
    assert service._source_meta("input_02") == ("file", True)


# -- media file browser ------------------------------------------------------
def test_browse_lists_media_files_and_dirs(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "clip.mp4").write_bytes(b"x")
    (tmp_path / "demo.MKV").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("ignore me", "utf-8")

    listing = browse_filesystem(str(tmp_path))
    names = [e["name"] for e in listing["entries"]]
    assert "sub" in names  # directory listed
    assert "clip.mp4" in names  # media file listed
    assert "demo.MKV" in names  # extension match is case-insensitive
    assert "notes.txt" not in names  # non-media filtered out
    # directories sort before files
    assert listing["entries"][0]["is_dir"] is True


def test_browse_file_path_lists_its_parent(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    listing = browse_filesystem(str(clip))
    assert listing["path"] == str(tmp_path)


def test_browse_empty_path_returns_roots():
    listing = browse_filesystem("")
    assert listing["is_root"] is True
    assert all(entry["is_dir"] for entry in listing["entries"])


def test_browse_missing_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        browse_filesystem(str(tmp_path / "does_not_exist"))


def test_config_snapshot_exposes_counts_and_source_fields(tmp_path):
    service = _service(tmp_path, input_count=3, output_count=3)
    snapshot = service.config_snapshot()
    assert snapshot["input_count"] == 3
    assert snapshot["output_count"] == 3
    first = snapshot["inputs"][0]
    assert "source_mode" in first
    assert "file_path" in first
    assert "source_url" in first
    assert "loop" in first
