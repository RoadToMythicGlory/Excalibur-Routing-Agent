"""Persistent program compositor for seamless cuts.

The legacy :class:`~obs_auto_director.matrix.media.output.ProgramOutputManager`
restarts FFmpeg on every cut, which breaks the output bitstream and forces OBS
to wait for a fresh keyframe -> a visible blip per switch.

This compositor instead keeps a *single* long-lived FFmpeg process that:

- decodes every routable input continuously (NVDEC via ``-hwaccel cuda``),
- normalizes each to one common resolution/fps/format plus a black slate,
- uses ``streamselect``/``astreamselect`` to pick the on-air input, and
- encodes once (NVENC) to a single continuous program stream.

A cut is then a runtime ``streamselect map <idx>`` command delivered to the
graph's ``zmq`` filter over a local ZMQ socket -- the output stream never stops,
so the cut is frame-accurate with no reconnect/blip. The FFmpeg process is only
respawned when input *membership* changes (an input is added/removed or its URL
changes), never on a normal cut.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from obs_auto_director.matrix.clock import now_ms
from obs_auto_director.matrix.media.ingest import (
    MediaInput,
    build_input_args,
    ffmpeg_available,
)

logger = logging.getLogger(__name__)

try:  # pyzmq is required to drive seamless switching; degrade gracefully if absent.
    import zmq
except Exception:  # pragma: no cover - exercised only when pyzmq is missing
    zmq = None  # type: ignore[assignment]

# OBS reads ONE stable source at this address; the compositor swaps the on-air
# input behind it without ever restarting the output stream.
DEFAULT_PROGRAM_PUBLISH_URL = "srt://0.0.0.0:9999?mode=listener&latency=200"

# Local ZMQ REP endpoint bound by FFmpeg's ``zmq`` filter; the manager connects a
# REQ socket here to broker runtime ``streamselect`` commands into the graph.
DEFAULT_ZMQ_ADDRESS = "tcp://127.0.0.1:5557"

# Stable filter instance names. FFmpeg's zmq broker matches commands against a
# filter's instance name, and auto-generated names (``Parsed_streamselect_0``)
# are not stable, so the select filters are named explicitly via ``@name``. The
# ``@name`` is appended to the type, so the addressable instance name (and thus
# the zmq command target) is the full ``streamselect@progv`` / ``astreamselect@proga``.
VIDEO_SELECT_LABEL = "progv"
AUDIO_SELECT_LABEL = "proga"
VIDEO_SELECT_TARGET = f"streamselect@{VIDEO_SELECT_LABEL}"
AUDIO_SELECT_TARGET = f"astreamselect@{AUDIO_SELECT_LABEL}"


def _free_tcp_port() -> int:
    """Pick a currently-free localhost TCP port for the zmq control socket.

    The zmq bind address must not collide with a lingering socket from a prior
    compositor instance; reusing a fixed port turns one stale bind into a
    permanent ``Address in use`` respawn loop. Allocating a fresh free port per
    spawn makes startup robust regardless of leftovers.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _escape_filter_value(value: str) -> str:
    """Escape ``:`` so a value survives FFmpeg's filtergraph parsing.

    The filter_complex string is unescaped twice (once splitting filterchains,
    once splitting a filter's ``:``-separated options), so a literal ``:`` must
    be written with two backslashes (``\\\\:``) to reach the option value intact.
    """
    return value.replace(":", "\\\\:")


def build_compositor_command(
    specs: list[MediaInput],
    publish_url: str,
    *,
    active_index: int = 0,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    zmq_address: str = DEFAULT_ZMQ_ADDRESS,
    hwaccel: str | None = "cuda",
    video_encoder: str = "h264_nvenc",
    include_audio: bool = True,
    video_bitrate: str = "6M",
) -> list[str]:
    """Build the persistent FFmpeg command for ``specs`` plus a black slate.

    The pad order fed to ``streamselect`` is ``specs`` (in list order) followed by
    the generated black slate, so the black slate is always index ``len(specs)``.
    ``active_index`` is baked into the filter at spawn time to avoid a switch race
    right after (re)spawn; later switches go through the ``zmq`` filter instead.
    """
    n = len(specs)
    black_idx = n
    args: list[str] = ["ffmpeg", "-nostdin", "-loglevel", "warning"]

    # Real inputs (NVDEC decode where available).
    for spec in specs:
        if hwaccel:
            args += ["-hwaccel", hwaccel]
        args += build_input_args(spec)

    # Generated fallbacks: a black video slate and (optionally) silent audio.
    args += ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}"]
    if include_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    norm = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps},setsar=1,format=yuv420p"
    )

    chains: list[str] = []
    vlabels: list[str] = []
    for i in range(n):
        chains.append(f"[{i}:v]{norm}[v{i}]")
        vlabels.append(f"[v{i}]")
    chains.append(f"[{black_idx}:v]{norm}[vblack]")
    vlabels.append("[vblack]")

    zmq_bind = _escape_filter_value(zmq_address)
    chains.append(
        f"{''.join(vlabels)}streamselect@{VIDEO_SELECT_LABEL}="
        f"inputs={n + 1}:map={active_index}[vsel]"
    )
    chains.append(f"[vsel]zmq=bind_address={zmq_bind}[vout]")

    if include_audio:
        anorm = "aformat=sample_rates=48000:channel_layouts=stereo,aresample=async=1"
        silent_idx = black_idx + 1
        alabels: list[str] = []
        for i in range(n):
            chains.append(f"[{i}:a]{anorm}[a{i}]")
            alabels.append(f"[a{i}]")
        chains.append(f"[{silent_idx}:a]{anorm}[asilent]")
        alabels.append("[asilent]")
        chains.append(
            f"{''.join(alabels)}astreamselect@{AUDIO_SELECT_LABEL}="
            f"inputs={n + 1}:map={active_index}[aout]"
        )

    args += ["-filter_complex", ";".join(chains)]
    args += ["-map", "[vout]"]
    if include_audio:
        args += ["-map", "[aout]"]

    args += [
        "-c:v",
        video_encoder,
        "-preset",
        "p1",
        "-tune",
        "ll",
        "-g",
        str(fps * 2),
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        video_bitrate,
    ]
    if include_audio:
        args += ["-c:a", "aac", "-b:a", "160k"]
    args += ["-f", "mpegts", publish_url]
    return args


def _spec_key(spec: MediaInput) -> tuple:
    return (spec.input_id, spec.read_url, spec.source_type, spec.loop)


@dataclass
class _SwitchClient:
    """Thin pyzmq REQ wrapper that brokers a command to the graph's zmq filter."""

    address: str
    _ctx: "zmq.Context | None" = None  # type: ignore[name-defined]

    def send(self, command: str, timeout_ms: int = 1000) -> bool:
        if zmq is None:  # pragma: no cover - missing optional dep
            logger.warning("pyzmq not installed; cannot send compositor switch command.")
            return False
        try:
            if self._ctx is None:
                self._ctx = zmq.Context.instance()
            sock = self._ctx.socket(zmq.REQ)
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
            sock.setsockopt(zmq.SNDTIMEO, timeout_ms)
            try:
                sock.connect(self.address)
                sock.send_string(command)
                sock.recv()  # ffmpeg's zmq filter always replies
                return True
            finally:
                sock.close(0)
        except Exception as exc:  # pragma: no cover - network/timing dependent
            logger.warning("compositor switch command failed (%s): %s", command, exc)
            return False


class CompositorOutputManager:
    """Single persistent FFmpeg compositor with seamless ZMQ-driven cuts.

    OBS points one media source at ``publish_url`` and never changes it; this
    manager keeps the output stream continuous and only changes which decoded
    input is mapped on air. It is a drop-in replacement for
    :class:`ProgramOutputManager` at the program path, but turns a cut from a
    process respawn into a single ``streamselect`` command.
    """

    def __init__(
        self,
        publish_url: str = DEFAULT_PROGRAM_PUBLISH_URL,
        *,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        zmq_address: str = DEFAULT_ZMQ_ADDRESS,
        hwaccel: str | None = "cuda",
        video_encoder: str = "h264_nvenc",
        include_audio: bool = True,
        restart_backoff_ms: float = 2000.0,
        log_path: str = ".preview/program_compositor.log",
    ) -> None:
        self.publish_url = publish_url
        self.log_path = log_path
        self.width = width
        self.height = height
        self.fps = fps
        self.zmq_address = zmq_address
        self.hwaccel = hwaccel
        self.video_encoder = video_encoder
        self.include_audio = include_audio
        self.restart_backoff_ms = restart_backoff_ms

        self.active = False
        self.current_input: str | None = None
        self.current_read_url: str | None = None

        self._specs: list[MediaInput] = []
        self._switch = _SwitchClient(zmq_address)
        self._process: subprocess.Popen | None = None
        self._last_started_at = 0.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------
    def enable(self) -> None:
        """Start compositing. Until enabled, inputs are tracked but FFmpeg idles."""
        with self._lock:
            if self.active:
                return
            self.active = True
            self._stop.clear()
            if self._specs:
                self._try_spawn()
            self._thread = threading.Thread(
                target=self._loop, name="program-compositor", daemon=True
            )
            self._thread.start()
        logger.info("Program compositor enabled -> %s", self.publish_url)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self.active = False
            self._kill()

    # -- inputs / switching --------------------------------------------------
    def set_inputs(self, specs: list[MediaInput]) -> None:
        """Define the set of routable inputs that make up the compositor graph.

        Membership/URL changes rebuild the filter graph (one respawn). Normal
        cuts do NOT call this -- they call :meth:`set_active`.
        """
        with self._lock:
            new_keys = [_spec_key(s) for s in specs]
            old_keys = [_spec_key(s) for s in self._specs]
            if new_keys == old_keys:
                return
            self._specs = list(specs)
            if not self.active:
                return
            if self._specs:
                self._try_spawn()
            else:
                self._kill()

    def set_active(self, input_id: str | None, read_url: str | None = None) -> None:
        """Switch the on-air input. Seamless when running (ZMQ), else remembered.

        Falls back to the black slate when ``input_id`` is unknown to the current
        graph (e.g. nothing routed or the input dropped to ``missing_signal``).
        """
        with self._lock:
            self.current_input = input_id
            self.current_read_url = read_url
            if not self.active or not self.is_running():
                return
            index = self._index_for(input_id)
            self._switch.send(f"{VIDEO_SELECT_TARGET} map {index}")
            if self.include_audio:
                self._switch.send(f"{AUDIO_SELECT_TARGET} map {index}")

    def _index_for(self, input_id: str | None) -> int:
        """Map an input id to its streamselect pad index; black slate if missing."""
        black_index = len(self._specs)
        if input_id is None:
            return black_index
        for i, spec in enumerate(self._specs):
            if spec.input_id == input_id:
                return i
        return black_index

    # -- status --------------------------------------------------------------
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
            "inputs": [s.input_id for s in self._specs],
        }

    # -- internal ------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._supervise()
            self._stop.wait(0.5)

    def _supervise(self) -> None:
        with self._lock:
            if not self.active or self._stop.is_set() or not self._specs:
                return
            if self._process is not None and self._process.poll() is None:
                return
            if now_ms() - self._last_started_at < self.restart_backoff_ms:
                return
            self._try_spawn()

    def _try_spawn(self) -> bool:
        if not self._specs:
            return False
        if not ffmpeg_available():
            logger.error("ffmpeg not found on PATH; cannot start program compositor.")
            return False
        self._kill()
        self._last_started_at = now_ms()
        # Bind the zmq control socket to a fresh free port each spawn so a
        # lingering socket from a previous instance can never wedge startup.
        self.zmq_address = f"tcp://127.0.0.1:{_free_tcp_port()}"
        self._switch.address = self.zmq_address
        active_index = self._index_for(self.current_input)
        command = build_compositor_command(
            self._specs,
            self.publish_url,
            active_index=active_index,
            width=self.width,
            height=self.height,
            fps=self.fps,
            zmq_address=self.zmq_address,
            hwaccel=self.hwaccel,
            video_encoder=self.video_encoder,
            include_audio=self.include_audio,
        )
        stderr_target = subprocess.DEVNULL
        try:
            log_file = Path(self.log_path)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            stderr_target = open(log_file, "w", encoding="utf-8")
        except OSError:  # pragma: no cover - fall back to discarding stderr
            stderr_target = subprocess.DEVNULL
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=stderr_target,
            stdin=subprocess.DEVNULL,
        )
        if stderr_target is not subprocess.DEVNULL:
            stderr_target.close()
        logger.info(
            "program compositor ffmpeg spawned: %d input(s) -> %s (active=%s)",
            len(self._specs),
            self.publish_url,
            self.current_input,
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
