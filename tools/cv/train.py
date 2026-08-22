#!/usr/bin/env python3
"""Train soccer role/event models. Heavy deps live in the cv-train extra."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train soccer CV models.")
    parser.add_argument("--splits", default="assets/datasets/soccer/splits")
    parser.add_argument("--task", choices=("roles", "events", "detect"), default="events")
    parser.add_argument("--out", default="src/excalibur/cv/models")
    parser.add_argument("--epochs", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "task": args.task,
        "epochs": args.epochs,
        "splits": args.splits,
        "status": "not_trained",
        "note": "Install extras: pip install -e .[cv-train] then re-run with real data.",
    }
    path = out / f"{args.task}.checkpoint.json"
    path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
