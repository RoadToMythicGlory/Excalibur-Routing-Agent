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

# CVE-2026-119610 (GStreamer SA-2026-0095): rtph264depay in gst-plugins-good
# before 1.28.9 has a remotely triggerable out-of-bounds heap read. RTSP inputs
# are decoded via rtspsrc ! decodebin3, which autoplugs rtph264depay.
MIN_PLUGINS_GOOD_VERSION: tuple[int, int, int] = (1, 28, 9)
# The plugin (from gst-plugins-good) that ships rtph264depay.
RTP_PLUGIN = "rtp"

_cache: dict[str, bool] = {}
_version_cache: dict[str, tuple[int, ...] | None] = {}


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
    version = plugins_good_version() if runtime else None
    vulnerable = vulnerable_to_cve_2026_119610() if runtime else False
    return {
        "gst_inspect": gst_inspect_path(),
        "runtime_available": runtime,
        "required": required,
        "optional": optional,
        "missing_required": missing,
        "plugins_good_version": ".".join(map(str, version)) if version else None,
        "vulnerable_cve_2026_119610": vulnerable,
        "ready": runtime and not missing and not vulnerable,
    }


def parse_plugin_version(inspect_output: str) -> tuple[int, ...] | None:
    """Extract ``(major, minor, micro)`` from ``gst-inspect-1.0 <plugin>`` output.

    The plugin details block contains a line like ``  Version  1.28.6``.
    """
    for line in inspect_output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "Version":
            numbers = parts[1].split(".")
            try:
                return tuple(int(n) for n in numbers[:3])
            except ValueError:
                return None
    return None


def plugins_good_version() -> tuple[int, ...] | None:
    """Version of the gst-plugins-good ``rtp`` plugin, or ``None`` if absent."""
    if RTP_PLUGIN in _version_cache:
        return _version_cache[RTP_PLUGIN]
    inspect = gst_inspect_path()
    version: tuple[int, ...] | None = None
    if inspect is not None:
        try:
            result = subprocess.run(
                [inspect, RTP_PLUGIN],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                version = parse_plugin_version(result.stdout)
        except (OSError, subprocess.SubprocessError) as error:  # pragma: no cover - env dependent
            logger.debug("gst-inspect version probe failed: %s", error)
    _version_cache[RTP_PLUGIN] = version
    return version


def vulnerable_to_cve_2026_119610() -> bool:
    """True when the installed rtp plugin is older than 1.28.9.

    An absent plugin is not vulnerable (the depayloader code is not there).
    """
    version = plugins_good_version()
    if version is None:
        return False
    padded = tuple(version) + (0,) * (3 - len(version))
    return padded < MIN_PLUGINS_GOOD_VERSION


def reset_cache() -> None:
    """Clear the memoized probe results (used by tests)."""
    _cache.clear()
    _version_cache.clear()
