from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from excalibur.matrix.frame_tap import FrameTap


def load_pixels(frame: FrameTap, width: int | None = None) -> np.ndarray | None:
    """Load BGR pixels from an in-memory array or a JPEG path in metadata."""
    if frame.pixels is not None:
        image = np.asarray(frame.pixels)
    else:
        path = frame.metadata.get("path")
        if not path or not Path(path).exists():
            return None
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            return None
    if width and image.shape[1] != width:
        scale = width / float(image.shape[1])
        height = max(1, int(image.shape[0] * scale))
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    return image


def sharpness_score(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    value = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return max(0.0, min(1.0, value / 400.0))


def coverage_score(image: np.ndarray) -> float:
    """Cheap pitch-green coverage heuristic used when no pitch model is loaded."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (35, 40, 40), (90, 255, 255))
    return float(np.mean(mask > 0))
