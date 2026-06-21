from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass

from obs_auto_director.matrix.clock import now_ms
from obs_auto_director.matrix.media.ingest import ffmpeg_available

logger = logging.getLogger(__name__)

# OBS reads ONE stable source at this address; Excalibur swaps the content behind
# it. Default binds an SRT listener so OBS connects as a caller with no extra
# software. Override per deployment (e.g. publish into MediaMTX) via config.
DEFAULT_PROGRAM_PUBLISH_URL = "srt://0.0.0.0:9999?mode=listener&latency=200"


def build_program_command(
    read_url: str, publish_url: str, codec: str = "copy"
) -> list[str]:
    """FFmpeg command to republish one input feed as the stable program output.

    ``codec="copy"`` keeps the source bitstream (lowest latency/CPU, full
    program quality). Pass a real encoder (e.g. ``"libx264"``) only when the
    source must be transcoded for OBS compatibility.
    """
    args = ["ffmpeg", "-nostdin", "-loglevel", "warning"]
    if read_url.startswith("rtsp://"):
        args += ["-rtsp_transport", "tcp"]
    args += ["-fflags", "nobuffer", "-i", read_url]
    if codec == "copy":
        args += ["-c", "copy"]
    else:
        args += [
            "-c:v",
            codec,
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-c:a",
            "aac",
        ]
    args += ["-f", "mpegts", publish_url]
    return args


def program_obs_url(publish_url: str, host_ip: str) -> str:
    """Convert a listener publish URL into the caller URL OBS should read.

    For non-listener publish URLs (e.g. publishing into MediaMTX) the URL is
    returned unchanged; the operator reads it back from that hub directly.
    """
    if "mode=listener" not in publish_url:
        return publish_url
    url = publish_url.replace("0.0.0.0", host_ip)
    url = url.replace("mode=listener", "mode=caller")
    return url


@dataclass
class MediaOutput:
    """A stable broadcast output: where to publish and how to encode it."""

    output_id: str
    publish_url: str
    codec: str = "copy"


class ProgramOutputManager:
    """Republishes the currently routed program input as one stable OBS feed.

    OBS points a single media source at ``publish_url`` and never changes it;
    this manager swaps the *content* by switching which input FFmpeg reads
    whenever the routing engine applies a new route to the program output.

    This is deliberately separate from the agent's observation path: the ingest
    manager produces low-res JPEG frame taps for analysis and the multiview,
    while this manager produces the full-quality program video. Analysis and
    broadcast quality therefore never share a pipeline.

    Switching inputs restarts the FFmpeg worker, so a cut causes a brief program
    transition; a persistent compositor is a later improvement.
    """

    def __init__(
        self,
        publish_url: str = DEFAULT_PROGRAM_PUBLISH_URL,
        codec: str = "copy",
        restart_backoff_ms: float = 2000.0,
    ) -> None:
        self.publish_url = publish_url
        self.codec = codec
        self.restart_backoff_ms = restart_backoff_ms
        self.active = False
        self.current_input: str | None = None
        self.current_read_url: str | None = None
        self._process: subprocess.Popen | None = None
        self._last_started_at = 0.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------
    def enable(self) -> None:
        """Start publishing. Until enabled, sources are tracked but not spawned.

        Tied to real media ingest: in simulate mode (no real feeds) the program
        output stays idle while still reporting its intended source for the UI.
        """
        with self._lock:
            if self.active:
                return
            self.active = True
            self._stop.clear()
            if self.current_read_url:
                self._try_spawn()
            self._thread = threading.Thread(
                target=self._loop, name="program-output", daemon=True
            )
            self._thread.start()
        logger.info("Program output publisher enabled -> %s", self.publish_url)

    def set_source(self, input_id: str | None, read_url: str | None) -> None:
        """Point the program output at a new input feed (route change)."""
        with self._lock:
            changed = read_url != self.current_read_url
            self.current_input = input_id
            self.current_read_url = read_url
            if not self.active or not changed:
                return
            if read_url:
                self._try_spawn()
            else:
                self._kill()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self.active = False
            self._kill()

    # -- read side -----------------------------------------------------------
    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def status(self) -> dict:
        return {
            "publish_url": self.publish_url,
            "source_input": self.current_input,
            "source_url": self.current_read_url,
            "running": self.is_running(),
            "active": self.active,
        }

    # -- internal ------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._supervise()
            self._stop.wait(0.5)

    def _supervise(self) -> None:
        with self._lock:
            if not self.active or self._stop.is_set() or not self.current_read_url:
                return
            if self._process is not None and self._process.poll() is None:
                return
            if now_ms() - self._last_started_at < self.restart_backoff_ms:
                return
            self._try_spawn()

    def _try_spawn(self) -> bool:
        if not self.current_read_url:
            return False
        if not ffmpeg_available():
            logger.error("ffmpeg not found on PATH; cannot publish program output.")
            return False
        self._kill()
        self._last_started_at = now_ms()
        self._process = subprocess.Popen(
            build_program_command(self.current_read_url, self.publish_url, self.codec),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        logger.info(
            "program output ffmpeg spawned: %s -> %s",
            self.current_read_url,
            self.publish_url,
        )
        return True

    def _kill(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self._process.kill()
        self._process = None
