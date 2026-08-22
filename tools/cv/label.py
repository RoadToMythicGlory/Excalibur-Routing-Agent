#!/usr/bin/env python3
"""Convert soccer labels between CVAT, Label Studio, and YOLO-style JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

EVENT_CLASSES = [
    "foul",
    "free_kick",
    "penalty_kick",
    "corner_kick",
    "burst_attack",
    "ordinary_pass",
    "open_play",
]
ROLE_CLASSES = [
    "main_wide",
    "tactical_high",
    "sideline_tight",
    "goal_line",
    "behind_goal_left",
    "behind_goal_right",
    "reaction",
    "unknown",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize soccer label exports.")
    parser.add_argument("input", help="CVAT/Label Studio JSON export")
    parser.add_argument("--out", default="assets/datasets/soccer/labels/normalized.json")
    parser.add_argument("--format", choices=("yolo", "coco", "intervals"), default="intervals")
    return parser


def normalize(raw: dict) -> dict:
    return {
        "classes": {"events": EVENT_CLASSES, "roles": ROLE_CLASSES, "objects": ["ball", "player", "referee"]},
        "items": raw.get("items") or raw.get("annotations") or [],
        "source_format": raw.get("format", "unknown"),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    normalized = normalize(raw if isinstance(raw, dict) else {"items": raw})
    normalized["target_format"] = args.format
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
