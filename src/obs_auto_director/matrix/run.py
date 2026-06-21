from __future__ import annotations

import argparse
import logging

from obs_auto_director.matrix.demo import (
    print_summary,
    run_clutch_demo,
    start_live_simulation,
)
from obs_auto_director.matrix.server import serve
from obs_auto_director.matrix.service import build_default_service


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="obs-matrix",
        description="Production video matrix: bus-driven CS2 director.",
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
        if args.simulate:
            start_live_simulation(service)
        serve(service, host=args.host, port=args.port)
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
