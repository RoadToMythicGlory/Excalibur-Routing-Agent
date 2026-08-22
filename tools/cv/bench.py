#!/usr/bin/env python3
"""Benchmark CPU vs GPU soccer inference profiles."""

from __future__ import annotations

import argparse
import json
import time

import numpy as np

from excalibur.cv.inference.mock_runner import MockInferenceEngine
from excalibur.cv.profiles import resolve_runtime_profile
from excalibur.matrix.soccer.config import load_soccer_profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark soccer inference profiles.")
    parser.add_argument("--profile", default="configs/games/soccer.json")
    parser.add_argument("--inputs", type=int, default=4)
    parser.add_argument("--frames", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile = load_soccer_profile(args.profile)
    results = {}
    engine = MockInferenceEngine()
    for name in ("cpu", "gpu"):
        runtime = resolve_runtime_profile(profile, name)
        image = np.zeros((runtime.width, runtime.width, 3), dtype=np.uint8)
        start = time.perf_counter()
        for _ in range(args.frames * args.inputs):
            engine.infer(image)
        elapsed = (time.perf_counter() - start) * 1000.0
        results[name] = {
            "width": runtime.width,
            "fps": runtime.fps,
            "stagger": runtime.stagger,
            "elapsed_ms": elapsed,
            "per_frame_ms": elapsed / max(1, args.frames * args.inputs),
        }
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
