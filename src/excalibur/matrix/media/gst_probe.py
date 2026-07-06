"""GStreamer runtime + plugin availability probing.

The GStreamer output path (see :mod:`excalibur.matrix.media.gstreamer_output`)
relies on a small C++ ``media-core`` process, but before Excalibur ever spawns
it we want a cheap, dependency-free way to answer two questions from Python:

1. Is a GStreamer runtime installed at all (``gst-inspect-1.0`` on PATH)?
2. Are the specific elements the output pipeline needs actually present?

This matters on Windows in particular: a stock GStreamer install does not always
ship a libsrt-enabled ``srtsrc``/``srtsink``, and the ``bad``/``libav`` plugin
sets (which carry ``x264enc`` and the software decoders) are optional components
in the MSI installer. Probing up front lets the manager degrade gracefully and
surface an actionable error instead of a cryptic pipeline failure.

The probe shells out to ``gst-inspect-1.0`` because it is the canonical way to
ask "does this element exist in the registry" without linking against GStreamer
from Python. Results are cached per element for the process lifetime.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)

GST_INSPECT = "gst-inspect-1.0"

# Elements the 1080p30 SRT output normalizer needs. Grouped by role so a missing
# element maps to a human-readable capability gap in :func:`describe_environment`.
REQUIRED_ELEMENTS: dict[str, str] = {
    "appsrc": "raw-frame injection into the encode side (app boundary)",
    "appsink": "raw-frame extraction from the decode side (app boundary)",
    "decodebin3": "codec-agnostic source decoding",
    "videoconvert": "pixel-format normalization",
    "videoscale": "resolution normalization to 1080p",
    "videorate": "framerate normalization to 30fps",
    "capsfilter": "pinning the negotiated raw caps at the app boundary",
    "queue": "decoupling decode and encode threads",
    "x264enc": "software H.264 encode (development-default output encoder)",
    "h264parse": "repeating stream headers for clean OBS reconnects",
    "mpegtsmux": "MPEG-TS muxing of the program feed",
    "srtsink": "SRT caller/listener output to OBS",
    "srtsrc": "SRT ingest of encoder feeds",
}

# Elements that are nice to have (hardware acceleration / extra source types) but
# whose absence must NOT be treated as a hard failure.
OPTIONAL_ELEMENTS: dict[str, str] = {
    "rtspsrc": "MediaMTX RTSP readback source",
    "uridecodebin3": "file / VOD source decoding",
    "tsdemux": "demuxing MPEG-TS SRT feeds",
    "nvh264enc": "NVIDIA hardware H.264 encode",
    "nvh264dec": "NVIDIA hardware H.264 decode",
    "d3d11h264dec": "Direct3D11 hardware H.264 decode",
    "avdec_h264": "software H.264 decode fallback (gst-libav)",
}

_cache: dict[str, bool] = {}


def gst_inspect_path() -> str | None:
    """Absolute path to ``gst-inspect-1.0`` if it is on PATH, else ``None``."""
    return shutil.which(GST_INSPECT)


def gstreamer_available() -> bool:
    """True when a GStreamer runtime (``gst-inspect-1.0``) is reachable on PATH."""
    return gst_inspect_path() is not None


def element_available(name: str) -> bool:
    """Whether a single GStreamer element is registered locally.

    ``gst-inspect-1.0 <element>`` exits 0 when the element exists in the plugin
    registry and non-zero otherwise, which is exactly the signal we want. The
    result is cached because a cold ``gst-inspect`` call is not free and the set
    of installed plugins does not change within a run.
    """
    if name in _cache:
        return _cache[name]
    inspect = gst_inspect_path()
    if inspect is None:
        _cache[name] = False
        return False
    try:
        result = subprocess.run(
            [inspect, "--exists", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        available = result.returncode == 0
    except (OSError, subprocess.SubprocessError) as error:  # pragma: no cover - env dependent
        logger.debug("gst-inspect probe for %s failed: %s", name, error)
        available = False
    _cache[name] = available
    return available


def probe_elements(names: list[str]) -> dict[str, bool]:
    """Probe a batch of elements, returning ``{name: available}``."""
    return {name: element_available(name) for name in names}


def missing_required() -> list[str]:
    """Required elements that are not present in the local registry.

    An empty list means the output normalizer's mandatory element set is fully
    satisfied. If GStreamer itself is missing, every required element is
    considered missing.
    """
    return [name for name in REQUIRED_ELEMENTS if not element_available(name)]


def describe_environment() -> dict:
    """A structured snapshot of the GStreamer environment for logs / the UI.

    Never raises: this is intended to be safe to call from a status endpoint
    even on a machine with no GStreamer at all.
    """
    runtime = gstreamer_available()
    required = probe_elements(list(REQUIRED_ELEMENTS)) if runtime else {
        name: False for name in REQUIRED_ELEMENTS
    }
    optional = probe_elements(list(OPTIONAL_ELEMENTS)) if runtime else {
        name: False for name in OPTIONAL_ELEMENTS
    }
    missing = [name for name, ok in required.items() if not ok]
    return {
        "gst_inspect": gst_inspect_path(),
        "runtime_available": runtime,
        "required": required,
        "optional": optional,
        "missing_required": missing,
        "ready": runtime and not missing,
    }


def reset_cache() -> None:
    """Clear the memoized probe results (used by tests)."""
    _cache.clear()
