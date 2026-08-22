from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from excalibur.cv.frame_loader import coverage_score, load_pixels, sharpness_score
from excalibur.matrix.clock import now_ms
from excalibur.matrix.frame_tap import FrameTap, SimulatedFrameTapSource


def test_load_pixels_from_jpeg(tmp_path):
    path = Path(tmp_path) / "input_01.jpg"
    image = np.zeros((180, 320, 3), dtype=np.uint8)
    image[:, :] = (20, 180, 20)
    cv2.imwrite(str(path), image)
    frame = FrameTap(
        input_id="input_01",
        width=0,
        height=0,
        captured_at=now_ms(),
        metadata={"path": str(path)},
    )
    loaded = load_pixels(frame, width=160)
    assert loaded is not None
    assert loaded.shape[1] == 160
    assert coverage_score(loaded) > 0.5
    assert 0 <= sharpness_score(loaded) <= 1


def test_simulated_source_has_no_pixels():
    frame = SimulatedFrameTapSource().grab("input_01")
    assert frame is not None
    assert frame.metadata["simulated"] is True
    assert frame.pixels is None
    assert load_pixels(frame) is None
