"""Python mirror of the media-core control protocol.

This module is the Python side of the newline-delimited JSON contract defined in
``native/media-core/src/control_protocol.hpp``. Keep the two in lockstep: the
command builders here must produce exactly what ``pipeline_spec.cpp``'s
``parse_command`` expects, and the state/type constants must match.

Nothing here talks to a subprocess; it only builds command dicts and interprets
response dicts, so it is trivially unit-testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# -- command names -----------------------------------------------------------
CMD_START = "start"
CMD_SET_INPUTS = "set_inputs"
CMD_TAKE = "take"
CMD_STATUS = "status"
CMD_STOP = "stop"

# -- message types -----------------------------------------------------------
MSG_ACK = "ack"
MSG_STATUS = "status"
MSG_ERROR = "error"

# -- lifecycle states --------------------------------------------------------
STATE_IDLE = "idle"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_RECONNECTING = "reconnecting"
STATE_STOPPED = "stopped"
STATE_ERROR = "error"

# -- defaults (mirror control_protocol.hpp) ----------------------------------
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30
DEFAULT_BITRATE_KBPS = 6000
DEFAULT_PIXEL_FORMAT = "I420"
DEFAULT_ENCODER = "x264enc"


@dataclass
class OutputConfig:
    """The normalized output shape media-core encodes every source to."""

    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    fps: int = DEFAULT_FPS
    pixel_format: str = DEFAULT_PIXEL_FORMAT
    encoder: str = DEFAULT_ENCODER
    video_bitrate_kbps: int = DEFAULT_BITRATE_KBPS
    publish_url: str = "srt://0.0.0.0:9999?mode=listener&latency=200"

    def to_start_payload(self) -> dict:
        return {
            "cmd": CMD_START,
            "publish_url": self.publish_url,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "video_bitrate_kbps": self.video_bitrate_kbps,
            "encoder": self.encoder,
            "pixel_format": self.pixel_format,
        }


@dataclass
class InputSpec:
    """One routable input, as media-core understands it."""

    id: str
    url: str
    source_type: str = "srt"
    loop: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "source_type": self.source_type,
            "loop": self.loop,
        }


def build_start(config: OutputConfig) -> dict:
    return config.to_start_payload()


def build_set_inputs(inputs: list[InputSpec]) -> dict:
    return {"cmd": CMD_SET_INPUTS, "inputs": [spec.to_dict() for spec in inputs]}


def build_take(input_id: str | None) -> dict:
    # ``None`` means "nothing on air" -> media-core holds the black slate.
    return {"cmd": CMD_TAKE, "input_id": input_id or ""}


def build_status() -> dict:
    return {"cmd": CMD_STATUS}


def build_stop() -> dict:
    return {"cmd": CMD_STOP}


def encode_command(command: dict) -> str:
    """Serialize a command dict to a single newline-terminated JSON line."""
    return json.dumps(command, separators=(",", ":")) + "\n"


@dataclass
class StatusReport:
    """Parsed ``type=status`` frame from media-core."""

    state: str = STATE_IDLE
    active_input: str | None = None
    negotiated_caps: str = ""
    encoder: str = ""
    output_url: str = ""
    fps: float = 0.0
    pushed_frames: int = 0
    dropped_frames: int = 0
    black_frames: int = 0
    last_error: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.state == STATE_RUNNING

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "active_input": self.active_input,
            "negotiated_caps": self.negotiated_caps,
            "encoder": self.encoder,
            "output_url": self.output_url,
            "fps": self.fps,
            "pushed_frames": self.pushed_frames,
            "dropped_frames": self.dropped_frames,
            "black_frames": self.black_frames,
            "last_error": self.last_error,
        }


def parse_message(line: str) -> dict | None:
    """Parse one response line into a dict, or ``None`` if it is not valid JSON."""
    line = line.strip()
    if not line:
        return None
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return None
    return message if isinstance(message, dict) else None


def parse_status(message: dict) -> StatusReport:
    """Interpret a ``type=status`` dict into a :class:`StatusReport`."""
    return StatusReport(
        state=str(message.get("state", STATE_IDLE)),
        active_input=message.get("active_input"),
        negotiated_caps=str(message.get("negotiated_caps", "")),
        encoder=str(message.get("encoder", "")),
        output_url=str(message.get("output_url", "")),
        fps=float(message.get("fps", 0.0) or 0.0),
        pushed_frames=int(message.get("pushed_frames", 0) or 0),
        dropped_frames=int(message.get("dropped_frames", 0) or 0),
        black_frames=int(message.get("black_frames", 0) or 0),
        last_error=message.get("last_error"),
        raw=message,
    )
