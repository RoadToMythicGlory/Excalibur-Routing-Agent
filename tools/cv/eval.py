#!/usr/bin/env python3
"""Evaluate a soccer model and emit a routing-gate manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from excalibur.matrix.soccer.config import load_soccer_profile
from excalibur.cv.inference.engine import ModelManifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate soccer CV models.")
    parser.add_argument(
        "--metrics",
        required=True,
        help=(
            "JSON with precision/recall/etc. Must contain \"validated\": true "
            "(set by a real held-out evaluation run) for the manifest to be "
            "eligible for live routing."
        ),
    )
    parser.add_argument("--out", default="src/excalibur/cv/models/soccer.manifest.json")
    parser.add_argument("--profile", default="configs/games/soccer.json")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--model-path", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
    profile = load_soccer_profile(args.profile)
    manifest = ModelManifest(
        version=args.version,
        path=args.model_path,
        classes=list(profile.events.keys()),
        metrics={
            "precision": float(metrics.get("precision", 0)),
            "recall": float(metrics.get("recall", 0)),
            "false_events_per_match": float(metrics.get("false_events_per_match", 99)),
            "inference_ms": float(metrics.get("inference_ms", 999)),
            "switch_rate_per_min": float(metrics.get("switch_rate_per_min", 99)),
        },
        # Only a metrics file that attests "validated": true (i.e. produced by
        # a real evaluation run over held-out matches) can mark the manifest
        # validated; this tool must never grant that flag on its own.
        validated=bool(metrics.get("validated", False)),
    )
    payload = {
        "version": manifest.version,
        "path": manifest.path,
        "classes": manifest.classes,
        "metrics": manifest.metrics,
        "validated": manifest.validated,
        "passes_gates": manifest.passes(profile.evaluation_gates),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["passes_gates"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
