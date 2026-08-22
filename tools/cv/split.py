#!/usr/bin/env python3
"""Split soccer labels by match id so validation never leaks a live game."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Split soccer labels by match.")
    parser.add_argument("labels", help="Normalized labels JSON with items[].match_id")
    parser.add_argument("--out", default="assets/datasets/soccer/splits")
    parser.add_argument("--train-ratio", type=float, default=0.7)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    items = payload.get("items") or []
    by_match: dict[str, list] = defaultdict(list)
    for item in items:
        by_match[str(item.get("match_id", "unknown"))].append(item)
    matches = sorted(by_match)
    train_cut = max(1, int(len(matches) * args.train_ratio)) if matches else 0
    val_cut = train_cut + int(len(matches) * args.val_ratio)
    splits = {
        "train": matches[:train_cut],
        "val": matches[train_cut:val_cut],
        "test": matches[val_cut:],
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, match_ids in splits.items():
        subset = [item for match_id in match_ids for item in by_match[match_id]]
        (out / f"{name}.json").write_text(
            json.dumps({"matches": match_ids, "items": subset}, indent=2),
            encoding="utf-8",
        )
    print(json.dumps({name: len(ids) for name, ids in splits.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
