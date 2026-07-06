#!/usr/bin/env python3
"""End-to-end smoke test for the GStreamer program output.

This drives the real ``media-core`` binary through
:class:`excalibur.matrix.media.gstreamer_output.GstProgramOutputManager` and
prints the status frames it reports back. It exercises the three behaviors the
plan calls out:

  * caps negotiation   - a real source is normalized to 1080p30/I420,
  * route switching     - takes input_01, then input_02 (if given), then black,
  * stalled fallback    - a dead/paused source falls back to black frames while
                          the SRT output keeps running (watch ``black_frames``).

It is intentionally NOT a pytest (it needs a GStreamer runtime, the compiled
binary, and a live source), so it lives under scripts/ and is run by hand:

    python native/media-core/scripts/smoke_test.py \
        --source rtsp://127.0.0.1:8554/input_01 \
        --source2 rtsp://127.0.0.1:8554/input_02 \
        --publish "srt://0.0.0.0:9999?mode=listener&latency=200"

Open the published SRT URL in OBS (as a caller) to eyeball the output.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Make the excalibur package importable when run straight from the repo.
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from excalibur.matrix.media.gst_probe import describe_environment  # noqa: E402
from excalibur.matrix.media.gstreamer_output import (  # noqa: E402
    GstProgramOutputManager,
    find_media_core,
)
from excalibur.matrix.media.ingest import MediaInput  # noqa: E402


def _source_type(url: str) -> str:
    if url.startswith("srt://"):
        return "srt"
    if url.startswith("rtsp://"):
        return "rtsp"
    if url.startswith(("http://", "https://")):
        return "url"
    return "file"


def _print_status(manager: GstProgramOutputManager, label: str) -> None:
    status = manager.status()
    print(
        f"[{label}] state={status['state']} active={status['source_input']} "
        f"pushed={status['pushed_frames']} black={status['black_frames']} "
        f"dropped={status['dropped_frames']} err={status['last_error']}"
    )


def _watch(manager: GstProgramOutputManager, label: str, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        _print_status(manager, label)
        time.sleep(1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="media-core GStreamer smoke test")
    parser.add_argument("--source", required=True, help="primary source URL (srt/rtsp/url/file)")
    parser.add_argument("--source2", help="optional second source URL to switch to")
    parser.add_argument(
        "--publish",
        default="srt://0.0.0.0:9999?mode=listener&latency=200",
        help="program output publish URL",
    )
    parser.add_argument("--dwell", type=float, default=5.0, help="seconds per phase")
    args = parser.parse_args()

    env = describe_environment()
    print("GStreamer environment:")
    print(f"  gst-inspect: {env['gst_inspect']}")
    print(f"  runtime ready: {env['ready']}")
    if env["missing_required"]:
        print(f"  MISSING required elements: {env['missing_required']}")

    binary = find_media_core()
    print(f"media-core binary: {binary}")
    if binary is None:
        print("Build native/media-core first (see its README). Aborting.")
        return 2
    if not env["ready"]:
        print("GStreamer runtime is incomplete; the pipeline may fail. Continuing anyway.")

    inputs = [
        MediaInput(input_id="input_01", read_url=args.source, source_type=_source_type(args.source))
    ]
    if args.source2:
        inputs.append(
            MediaInput(
                input_id="input_02",
                read_url=args.source2,
                source_type=_source_type(args.source2),
            )
        )

    manager = GstProgramOutputManager(args.publish)
    manager.set_inputs(inputs)
    manager.enable()
    print(f"Publishing normalized 1080p30 program to: {args.publish}")

    try:
        manager.set_active("input_01", args.source)
        _watch(manager, "take input_01", args.dwell)

        if args.source2:
            manager.set_active("input_02", args.source2)
            _watch(manager, "take input_02", args.dwell)

        # Stalled fallback: route to nothing -> output must hold on black frames.
        manager.set_active(None, None)
        _watch(manager, "black slate", args.dwell)
    finally:
        manager.stop()
        print("stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
