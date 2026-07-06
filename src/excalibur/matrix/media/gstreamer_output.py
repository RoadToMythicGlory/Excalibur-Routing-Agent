"""GStreamer-backed program output manager.

This is a drop-in alternative to
:class:`excalibur.matrix.media.compositor.CompositorOutputManager`. Instead of
driving FFmpeg directly, it supervises the C++ ``media-core`` process (see
``native/media-core/``) and talks to it over the newline-delimited JSON protocol
in :mod:`excalibur.matrix.media.media_core_protocol`.

media-core normalizes whatever input is on air to a fixed 1080p30/I420 stream
and republishes it as one stable MPEG-TS-over-SRT feed, so OBS keeps reading a
single URL while cuts happen behind it. The Python side is deliberately thin:
process lifecycle, command marshalling, and status mirroring. All the real media
work (caps negotiation, appsink/appsrc, encode) lives in C++.

The public surface mirrors ``CompositorOutputManager`` exactly so
``MatrixService`` can select either backend without any other code changes:
``publish_url``, ``current_input``, ``current_read_url``, ``active``,
``enable()``, ``stop()``, ``set_inputs()``, ``set_active()``, ``is_running()``,
``status()``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from excalibur.matrix.clock import now_ms
from excalibur.matrix.media import media_core_protocol as proto
from excalibur.matrix.media.gst_probe import describe_environment
from excalibur.matrix.media.ingest import MediaInput

logger = logging.getLogger(__name__)

DEFAULT_PROGRAM_PUBLISH_URL = "srt://0.0.0.0:9999?mode=listener&latency=200"

# Environment variable that pins the media-core binary location.
MEDIA_CORE_ENV = "EXCALIBUR_MEDIA_CORE"

# Repo-relative locations the build drops the binary into, checked in order.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_CANDIDATE_SUBPATHS = (
    "native/media-core/build/media-core",
    "native/media-core/build/media-core.exe",
    "native/media-core/build/Release/media-core.exe",
    "native/media-core/build/Debug/media-core.exe",
)


def find_media_core() -> str | None:
    """Locate the media-core executable.

    Resolution order: the ``EXCALIBUR_MEDIA_CORE`` env var, then well-known repo
    build outputs, then ``media-core`` on PATH. Returns ``None`` when nothing is
    found so the manager can degrade gracefully instead of raising.
    """
    override = os.environ.get(MEDIA_CORE_ENV)
    if override and Path(override).exists():
        return override
    for sub in _CANDIDATE_SUBPATHS:
        candidate = _REPO_ROOT / sub
        if candidate.exists():
            return str(candidate)
    return shutil.which("media-core")


def _to_media_core_input(spec: MediaInput) -> proto.InputSpec:
    """Convert an ingest ``MediaInput`` into the media-core input schema.

    Local file paths are turned into ``file://`` URIs because media-core opens
    file sources with ``uridecodebin3``, which expects a URI rather than a bare
    Windows path.
    """
    url = spec.read_url
    if spec.source_type == "file":
        path = Path(spec.read_url)
        if path.exists() or not url.startswith(("file://", "http://", "https://")):
            try:
                url = path.as_uri()
            except ValueError:  # pragma: no cover - relative path fallback
                url = spec.read_url
    return proto.InputSpec(
        id=spec.input_id,
        url=url,
        source_type=spec.source_type,
        loop=spec.loop,
    )


class GstProgramOutputManager:
    """Supervises media-core and mirrors the FFmpeg compositor's interface."""

    def __init__(
        self,
        publish_url: str = DEFAULT_PROGRAM_PUBLISH_URL,
        *,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        pixel_format: str = "I420",
        encoder: str = "x264enc",
        video_bitrate_kbps: int = 6000,
        binary_path: str | None = None,
        restart_backoff_ms: float = 2000.0,
        log_path: str = ".preview/media_core.log",
    ) -> None:
        self.publish_url = publish_url
        self.config = proto.OutputConfig(
            width=width,
            height=height,
            fps=fps,
            pixel_format=pixel_format,
            encoder=encoder,
            video_bitrate_kbps=video_bitrate_kbps,
            publish_url=publish_url,
        )
        self.binary_path = binary_path
        self.restart_backoff_ms = restart_backoff_ms
        self.log_path = log_path

        self.active = False
        self.current_input: str | None = None
        self.current_read_url: str | None = None

        self._specs: list[MediaInput] = []
        self._process: subprocess.Popen | None = None
        self._last_started_at = 0.0
        self._last_status: proto.StatusReport = proto.StatusReport()
        self._last_error: str | None = None
        self._log_file = None

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reader: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------
    def enable(self) -> None:
        """Start supervising media-core. Idempotent."""
        with self._lock:
            if self.active:
                return
            self.active = True
            self._stop.clear()
            self._try_spawn()
            self._thread = threading.Thread(
                target=self._loop, name="gst-program-output", daemon=True
            )
            self._thread.start()
        logger.info("GStreamer program output enabled -> %s", self.publish_url)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self.active = False
            self._send(proto.build_stop())
            self._kill()

    # -- inputs / switching --------------------------------------------------
    def set_inputs(self, specs: list[MediaInput]) -> None:
        """Define the set of routable inputs media-core may put on air."""
        with self._lock:
            new_keys = [(s.input_id, s.read_url, s.source_type, s.loop) for s in specs]
            old_keys = [(s.input_id, s.read_url, s.source_type, s.loop) for s in self._specs]
            if new_keys == old_keys:
                return
            self._specs = list(specs)
            if self.active and self.is_running():
                self._send_set_inputs()

    def set_active(self, input_id: str | None, read_url: str | None = None) -> None:
        """Switch the on-air input. Sends a ``take``; remembered if not running."""
        with self._lock:
            self.current_input = input_id
            self.current_read_url = read_url
            if not self.active or not self.is_running():
                return
            self._send(proto.build_take(input_id))

    # -- status --------------------------------------------------------------
    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def status(self) -> dict:
        with self._lock:
            gst = self._last_status
            return {
                "backend": "gstreamer",
                "publish_url": self.publish_url,
                "source_input": self.current_input,
                "source_url": self.current_read_url,
                "running": self.is_running(),
                "active": self.active,
                "inputs": [s.input_id for s in self._specs],
                "state": gst.state,
                "negotiated_caps": gst.negotiated_caps,
                "encoder": gst.encoder or self.config.encoder,
                "fps": gst.fps,
                "pushed_frames": gst.pushed_frames,
                "dropped_frames": gst.dropped_frames,
                "black_frames": gst.black_frames,
                "last_error": self._last_error,
            }

    def environment(self) -> dict:
        """GStreamer + binary readiness snapshot for diagnostics."""
        env = describe_environment()
        env["media_core_binary"] = self.binary_path or find_media_core()
        return env

    # -- internal ------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._supervise()
            self._stop.wait(0.5)

    def _supervise(self) -> None:
        with self._lock:
            if not self.active or self._stop.is_set():
                return
            if self._process is not None and self._process.poll() is None:
                return
            if now_ms() - self._last_started_at < self.restart_backoff_ms:
                return
            self._try_spawn()

    def _try_spawn(self) -> bool:
        binary = self.binary_path or find_media_core()
        if binary is None:
            logger.error(
                "media-core binary not found (set %s or build native/media-core); "
                "GStreamer program output cannot start.",
                MEDIA_CORE_ENV,
            )
            return False
        self.binary_path = binary
        self._kill()
        self._last_started_at = now_ms()

        stderr_target = subprocess.DEVNULL
        try:
            log_file = Path(self.log_path)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(log_file, "w", encoding="utf-8")
            stderr_target = self._log_file
        except OSError:  # pragma: no cover - fall back to discarding stderr
            self._log_file = None

        try:
            self._process = subprocess.Popen(
                [binary],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_target,
                text=True,
                bufsize=1,
            )
        except OSError as error:  # pragma: no cover - spawn failure is env dependent
            logger.error("failed to spawn media-core (%s): %s", binary, error)
            return False

        self._reader = threading.Thread(
            target=self._read_loop, args=(self._process,), name="gst-reader", daemon=True
        )
        self._reader.start()

        # Bring the freshly spawned process up to the desired state.
        self._send(proto.build_start(self.config))
        self._send_set_inputs()
        if self.current_input is not None:
            self._send(proto.build_take(self.current_input))

        logger.info(
            "media-core spawned: %s -> %s (%d input(s), active=%s)",
            binary,
            self.publish_url,
            len(self._specs),
            self.current_input,
        )
        return True

    def _send_set_inputs(self) -> None:
        inputs = [_to_media_core_input(s) for s in self._specs]
        self._send(proto.build_set_inputs(inputs))

    def _send(self, command: dict) -> None:
        """Write one JSON command to media-core's stdin (best-effort)."""
        proc = self._process
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return
        try:
            proc.stdin.write(proto.encode_command(command))
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as error:  # pragma: no cover - race on teardown
            logger.debug("media-core stdin write failed: %s", error)

    def _read_loop(self, proc: subprocess.Popen) -> None:
        """Consume media-core stdout, mirroring status/error frames into state."""
        stdout = proc.stdout
        if stdout is None:
            return
        for line in stdout:
            message = proto.parse_message(line)
            if message is None:
                continue
            mtype = message.get("type")
            if mtype == proto.MSG_STATUS:
                with self._lock:
                    self._last_status = proto.parse_status(message)
                    if self._last_status.last_error:
                        self._last_error = self._last_status.last_error
            elif mtype == proto.MSG_ERROR:
                text = str(message.get("message", "media-core error"))
                with self._lock:
                    self._last_error = text
                logger.warning(
                    "media-core error (%s): %s",
                    message.get("element", "?"),
                    text,
                )

    def _kill(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self._process.kill()
        self._process = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:  # pragma: no cover - defensive
                pass
            self._log_file = None
