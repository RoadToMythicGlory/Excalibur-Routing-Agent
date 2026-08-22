#!/usr/bin/env python3
"""Extract labeled-rate frames from soccer match clips."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract soccer dataset frames.")
    parser.add_argument("source", help="Video file or directory of clips")
    parser.add_argument("--out", default="assets/datasets/soccer/frames")
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--match-id", default="unspecified")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = Path(args.source)
    out = Path(args.out) / args.match_id
    out.mkdir(parents=True, exist_ok=True)
    clips = [source] if source.is_file() else sorted(source.glob("*.mp4"))
    manifest = {
        "match_id": args.match_id,
        "fps": args.fps,
        "clips": [str(clip) for clip in clips],
        "output": str(out),
        "note": "Use ffmpeg: ffmpeg -i CLIP -vf fps=FPS out/%06d.jpg",
    }
    (out / "extract_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
