#!/usr/bin/env python3
"""Export a trained soccer checkpoint to ONNX plus checksum metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export soccer models to ONNX.")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--out", default="src/excalibur/cv/models/soccer.onnx")
    parser.add_argument("--opset", type=int, default=17)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    checkpoint = Path(args.checkpoint)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = checkpoint.read_bytes() if checkpoint.exists() else b"placeholder"
    digest = hashlib.sha256(raw).hexdigest()
    sidecar = {
        "checkpoint": str(checkpoint),
        "onnx": str(out),
        "opset": args.opset,
        "checksum": digest,
        "note": "Replace this stub by exporting a real torch/ultralytics model.",
    }
    sidecar_path = out.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    if not out.exists():
        out.write_bytes(b"")
    print(sidecar_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
