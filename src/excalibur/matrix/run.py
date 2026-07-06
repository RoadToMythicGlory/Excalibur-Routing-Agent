from __future__ import annotations

import argparse
import logging

from excalibur.config import load_yaml
from excalibur.matrix.demo import (
    print_summary,
    run_clutch_demo,
    start_live_simulation,
)
from excalibur.matrix.media.ingest import MediaInput
from excalibur.matrix.server import serve
from excalibur.matrix.service import build_default_service


def load_media_inputs(path: str) -> tuple[list[MediaInput], str, float]:
    data = load_yaml(path)
    preview_dir = data.get("preview_dir", ".preview")
    freshness_ms = float(data.get("freshness_ms", 2000.0))
    inputs = [
        MediaInput(
            input_id=item["id"],
            read_url=item["read_url"],
            width=int(item.get("width", 480)),
            fps=float(item.get("fps", 5.0)),
            quality=int(item.get("quality", 7)),
            source_type=str(item.get("source_type", "srt")),
            loop=bool(item.get("loop", False)),
        )
        for item in data.get("inputs", [])
    ]
    return inputs, preview_dir, freshness_ms


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="excalibur-matrix",
        description="Excalibur production video matrix: bus-driven CS2 director.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sim = sub.add_parser("simulate", help="Run the simulated clutch round end-to-end.")
    sim.add_argument("--live", action="store_true", help="Send real OBS WebSocket commands.")

    srv = sub.add_parser("serve", help="Run the control API for the Next.js control plane.")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8800)
    srv.add_argument("--live", action="store_true", help="Send real OBS WebSocket commands.")
    srv.add_argument(
        "--simulate",
        action="store_true",
        help="Continuously feed the simulated clutch round so the UI shows live data.",
    )
    srv.add_argument(
        "--media",
        metavar="MEDIA_CONFIG",
        help="Path to a media config YAML to enable real FFmpeg/SRT ingest.",
    )

    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "simulate":
        service = run_clutch_demo(obs_dry_run=not args.live)
        print_summary(service)
        return 0

    if args.command == "serve":
        service = build_default_service(obs_dry_run=not args.live)
        service.connect_obs()
        if args.media:
            inputs, preview_dir, freshness_ms = load_media_inputs(args.media)
            service.enable_media(inputs, preview_dir=preview_dir, freshness_ms=freshness_ms)
        service.start_director()
        if args.simulate:
            start_live_simulation(service)
        serve(service, host=args.host, port=args.port)
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
