from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SLOT_COUNT = 11

# Default virtual-camera (akvirtualcamera) settings. ``enabled`` is off until the
# operator explicitly turns it on, since it requires a one-time device
# registration on the broadcast machine.
DEFAULT_VIRTUALCAM = {
    "enabled": False,
    "device": "Excalibur",
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "manager_bin": "AkVCamManager",
}

VIRTUALCAM_FIELDS = ("enabled", "device", "width", "height", "fps", "manager_bin")

# Program-output backend selection. The FFmpeg compositor stays the default
# until the GStreamer media-core path is verified on a given deployment.
BACKEND_FFMPEG_COMPOSITOR = "ffmpeg_compositor"
BACKEND_GSTREAMER = "gstreamer"
PROGRAM_OUTPUT_BACKENDS = (BACKEND_FFMPEG_COMPOSITOR, BACKEND_GSTREAMER)

# Normalization shape applied by the program output (both backends honor it).
DEFAULT_PROGRAM_OUTPUT = {
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "pixel_format": "I420",
    "encoder": "x264enc",
    "video_bitrate_kbps": 6000,
}

PROGRAM_OUTPUT_INT_FIELDS = ("width", "height", "fps", "video_bitrate_kbps")
PROGRAM_OUTPUT_STR_FIELDS = ("pixel_format", "encoder")
PROGRAM_OUTPUT_FIELDS = PROGRAM_OUTPUT_INT_FIELDS + PROGRAM_OUTPUT_STR_FIELDS

INPUT_FIELDS = (
    "player_name",
    "team",
    "role",
    "obs_scene",
    # Source selection. `source_mode` is the modern field; `srt_mode` is kept for
    # backward compatibility with configs written before file/VOD support.
    "source_mode",
    "srt_mode",
    "srt_host",
    "srt_port",
    "srt_latency",
    # File / URL / RTSP playback.
    "file_path",
    "source_url",
    "loop",
)


class MatrixConfigStore:
    """Operator configuration persisted to a JSON file.

    Holds per-input metadata (player name, team, role, OBS scene) and the program
    output selection. The matrix ships with no real identities; everything here
    is set by the operator and survives restarts.
    """

    def __init__(self, path: str = "configs/matrix.runtime.json") -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data: dict = {
            "program_output": "output_01",
            "program_publish_url": None,
            # Which engine republishes the program feed, plus its normalized
            # output shape (resolution/fps/format/encoder/bitrate).
            "program_output_backend": BACKEND_FFMPEG_COMPOSITOR,
            "program_output_settings": dict(DEFAULT_PROGRAM_OUTPUT),
            "input_count": DEFAULT_SLOT_COUNT,
            "output_count": DEFAULT_SLOT_COUNT,
            # Whether the AI director is allowed to drive routing. When False the
            # agent is fully passive and only operator commands move the program.
            "agent_enabled": True,
            "inputs": {},
            # Optional: also expose the program feed as a Windows virtual-camera
            # device (akvirtualcamera) so OBS can pick "Excalibur" from the
            # Video Capture Device list. Disabled by default.
            "virtualcam": dict(DEFAULT_VIRTUALCAM),
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data["program_output"] = loaded.get("program_output", "output_01")
                self.data["program_publish_url"] = loaded.get("program_publish_url")
                self.data["program_output_backend"] = _coerce_backend(
                    loaded.get("program_output_backend")
                )
                settings = dict(DEFAULT_PROGRAM_OUTPUT)
                loaded_settings = loaded.get("program_output_settings")
                if isinstance(loaded_settings, dict):
                    settings.update(
                        _coerce_program_output(loaded_settings)
                    )
                self.data["program_output_settings"] = settings
                # Missing counts default to 11 so pre-existing runtime files load
                # without any manual migration.
                self.data["input_count"] = _coerce_count(
                    loaded.get("input_count"), DEFAULT_SLOT_COUNT
                )
                self.data["output_count"] = _coerce_count(
                    loaded.get("output_count"), DEFAULT_SLOT_COUNT
                )
                self.data["agent_enabled"] = _coerce_bool(
                    loaded.get("agent_enabled", True)
                )
                self.data["inputs"] = loaded.get("inputs", {}) or {}
                vcam = dict(DEFAULT_VIRTUALCAM)
                loaded_vcam = loaded.get("virtualcam")
                if isinstance(loaded_vcam, dict):
                    vcam.update(
                        {k: v for k, v in loaded_vcam.items() if k in VIRTUALCAM_FIELDS}
                    )
                self.data["virtualcam"] = vcam
                logger.info("Loaded matrix config from %s", self.path)
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("Could not load matrix config %s: %s", self.path, error)

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def set_input(self, input_id: str, fields: dict) -> dict:
        clean = {key: value for key, value in fields.items() if key in INPUT_FIELDS}
        current = self.data["inputs"].get(input_id, {})
        current.update(clean)
        self.data["inputs"][input_id] = current
        self.save()
        return current

    def input_config(self, input_id: str) -> dict:
        return self.data["inputs"].get(input_id, {})

    def all_inputs(self) -> dict:
        return dict(self.data["inputs"])

    def drop_input(self, input_id: str) -> None:
        """Forget persisted config for a slot that no longer exists."""
        if input_id in self.data["inputs"]:
            del self.data["inputs"][input_id]
            self.save()

    @property
    def input_count(self) -> int:
        return _coerce_count(self.data.get("input_count"), DEFAULT_SLOT_COUNT)

    @property
    def output_count(self) -> int:
        return _coerce_count(self.data.get("output_count"), DEFAULT_SLOT_COUNT)

    def set_counts(self, input_count: int, output_count: int) -> None:
        self.data["input_count"] = _coerce_count(input_count, DEFAULT_SLOT_COUNT)
        self.data["output_count"] = _coerce_count(output_count, DEFAULT_SLOT_COUNT)
        self.save()

    @property
    def agent_enabled(self) -> bool:
        return _coerce_bool(self.data.get("agent_enabled", True))

    def set_agent_enabled(self, enabled: bool) -> bool:
        value = _coerce_bool(enabled)
        self.data["agent_enabled"] = value
        self.save()
        return value

    @property
    def program_output(self) -> str:
        return self.data.get("program_output", "output_01")

    def set_program_output(self, output_id: str) -> None:
        self.data["program_output"] = output_id
        self.save()

    @property
    def program_publish_url(self) -> str | None:
        return self.data.get("program_publish_url")

    def set_program_publish_url(self, publish_url: str | None) -> None:
        self.data["program_publish_url"] = publish_url
        self.save()

    @property
    def program_output_backend(self) -> str:
        return _coerce_backend(self.data.get("program_output_backend"))

    def set_program_output_backend(self, backend: str) -> str:
        value = _coerce_backend(backend)
        self.data["program_output_backend"] = value
        self.save()
        return value

    @property
    def program_output_settings(self) -> dict:
        settings = dict(DEFAULT_PROGRAM_OUTPUT)
        stored = self.data.get("program_output_settings")
        if isinstance(stored, dict):
            settings.update(_coerce_program_output(stored))
        return settings

    def set_program_output_settings(self, fields: dict) -> dict:
        current = self.program_output_settings
        current.update(_coerce_program_output(fields))
        self.data["program_output_settings"] = current
        self.save()
        return current

    @property
    def virtualcam(self) -> dict:
        vcam = dict(DEFAULT_VIRTUALCAM)
        stored = self.data.get("virtualcam")
        if isinstance(stored, dict):
            vcam.update({k: v for k, v in stored.items() if k in VIRTUALCAM_FIELDS})
        return vcam

    def set_virtualcam(self, fields: dict) -> dict:
        """Update virtual-camera settings, coercing types and persisting."""
        current = self.virtualcam
        for key in VIRTUALCAM_FIELDS:
            if key not in fields or fields[key] is None:
                continue
            value = fields[key]
            if key == "enabled":
                current[key] = _coerce_bool(value)
            elif key in ("width", "height", "fps"):
                current[key] = _coerce_count(value, DEFAULT_VIRTUALCAM[key])
            else:
                current[key] = str(value).strip() or DEFAULT_VIRTUALCAM[key]
        self.data["virtualcam"] = current
        self.save()
        return current


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def _coerce_backend(value: object) -> str:
    """Normalize the program-output backend name, defaulting to the compositor."""
    if isinstance(value, str) and value.strip().lower() in PROGRAM_OUTPUT_BACKENDS:
        return value.strip().lower()
    return BACKEND_FFMPEG_COMPOSITOR


def _coerce_program_output(fields: dict) -> dict:
    """Coerce program-output normalization settings to their expected types."""
    clean: dict = {}
    for key in PROGRAM_OUTPUT_INT_FIELDS:
        if key in fields and fields[key] is not None:
            clean[key] = _coerce_count(fields[key], DEFAULT_PROGRAM_OUTPUT[key])
    for key in PROGRAM_OUTPUT_STR_FIELDS:
        if key in fields and fields[key] is not None:
            text = str(fields[key]).strip()
            if text:
                clean[key] = text
    return clean


def _coerce_count(value: object, default: int) -> int:
    """Parse a slot count, falling back to the default for missing/invalid input."""
    try:
        count = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return count if count >= 1 else default
