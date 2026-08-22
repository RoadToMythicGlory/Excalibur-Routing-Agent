from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from excalibur.matrix.clock import now_ms
from excalibur.matrix.domain import SlotState
from excalibur.matrix.frame_tap import FrameTap
from excalibur.matrix.matrix_core import MatrixCore

logger = logging.getLogger(__name__)


SOURCE_TYPES = ("srt", "rtsp", "url", "file")


@dataclass
class MediaInput:
    """One real feed: how to read it and where to write its preview frame.

    ``source_type`` selects how FFmpeg should open ``read_url``:
      - ``srt`` / ``url``: live stream, read with low buffering.
      - ``rtsp``: live stream over RTSP (TCP transport).
      - ``file``: a local video file / VOD, played back in real time (``-re``)
        and optionally looped (``loop``) for repeatable local testing or
        director-provided video bits.
    """

    input_id: str
    read_url: str
    width: int = 480
    fps: float = 5.0
    quality: int = 7  # ffmpeg -q:v, lower is better
    source_type: str = "srt"
    loop: bool = False


@dataclass
class _Worker:
    spec: MediaInput
    preview_path: Path
    # For most sources ``process`` is the single ffmpeg that reads the feed and
    # writes the preview JPEG. For SRT inputs the work is split in two (see
    # ``MediaIngestManager._spawn_srt``): ``process`` becomes a lightweight
    # *receiver* (SRT listener -> local UDP, stream copy, no decode) and
    # ``preview_process`` reads that local relay to write the JPEG. Splitting
    # them keeps the live SRT socket free of per-frame backpressure so a preview
    # hiccup can never drop the remote sender's connection.
    process: subprocess.Popen | None = None
    preview_process: subprocess.Popen | None = None
    relay_port: int | None = None
    last_started_at: float = 0.0
    preview_last_started_at: float = 0.0


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def build_srt_url(
    mode: str, host: str | None, port: int, latency_ms: int = 200
) -> str:
    """Build an SRT read URL for a listener (bind a port) or caller (dial host:port)."""
    if mode == "caller":
        target = host or "127.0.0.1"
        return f"srt://{target}:{port}?mode=caller&latency={latency_ms}"
    return f"srt://0.0.0.0:{port}?mode=listener&latency={latency_ms}"


def build_input_args(spec: "MediaInput") -> list[str]:
    """FFmpeg input-side args for one feed, based on its source type.

    File sources are played in real time (``-re``) so a long VOD does not flush
    through instantly, and may be looped. Live sources (srt/rtsp/url) read with
    low buffering; RTSP additionally forces TCP transport.
    """
    args: list[str] = []
    if spec.source_type == "file":
        if spec.loop:
            # Input-side loop: must precede -i. -1 loops forever.
            args += ["-stream_loop", "-1"]
        args += ["-re"]
    else:
        if spec.source_type == "rtsp" or spec.read_url.startswith("rtsp://"):
            args += ["-rtsp_transport", "tcp"]
        args += ["-fflags", "nobuffer"]
    args += ["-i", spec.read_url]
    return args


def build_ingest_command(spec: "MediaInput", preview_path: Path) -> list[str]:
    """Full FFmpeg command that turns one feed into a refreshed JPEG frame tap.

    The output is pinned to ``yuvj420p`` and single-threaded encoding on purpose.
    Hardware encoders (e.g. a Galahad NVENC contribution feed) deliver full-range
    YUV; without ``format=yuvj420p`` the MJPEG encoder logs "Non full-range YUV is
    non-standard" and, combined with its frame-threaded init, intermittently fails
    with ``ff_frame_thread_encoder_init failed`` / ``-22``. That failure kills the
    whole ffmpeg, which for an SRT *listener* input tears down the live sender's
    connection and triggers a reconnect storm. Forcing the JPEG-native pixel
    format and ``-threads 1`` makes the preview encoder init deterministically so
    the listener stays up across the feed's lifetime.
    """
    args = ["ffmpeg", "-nostdin", "-loglevel", "warning"]
    args += build_input_args(spec)
    args += [
        "-an",
        "-vf",
        f"fps={spec.fps},scale={spec.width}:-2,format=yuvj420p",
        "-threads",
        "1",
        "-q:v",
        str(spec.quality),
        "-update",
        "1",
        "-y",
        str(preview_path),
    ]
    return args


def _free_udp_port() -> int:
    """Pick a currently-free UDP port for a localhost relay between receiver/preview."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


# Localhost multicast group used for the receiver -> preview relay. Multicast is
# deliberate: unlike a unicast localhost UDP send, a multicast send never
# triggers an ICMP "port unreachable" when no one is currently listening, so on
# Windows the receiver's socket never gets WSAECONNRESET and the receiver does
# NOT die just because the preview process happens to be restarting. Each input
# gets its own port, and ``ttl=0`` keeps traffic on this host.
RELAY_MULTICAST_GROUP = "239.255.42.99"


def relay_send_url(relay_port: int) -> str:
    return (
        f"udp://{RELAY_MULTICAST_GROUP}:{relay_port}"
        f"?pkt_size=1316&ttl=0&localaddr=127.0.0.1"
    )


def relay_recv_url(relay_port: int) -> str:
    return (
        f"udp://{RELAY_MULTICAST_GROUP}:{relay_port}"
        f"?localaddr=127.0.0.1&overrun_nonfatal=1&fifo_size=1000000"
    )


def build_srt_receiver_command(spec: "MediaInput", relay_url: str) -> list[str]:
    """Lightweight SRT receiver: pull the live feed and relay it locally, verbatim.

    This process does **no** decoding or per-frame work -- it stream-copies the
    incoming MPEG-TS straight to a localhost multicast relay (``relay_url``).
    Because it drains the SRT socket as fast as the network delivers, it never
    applies backpressure to the remote sender (e.g. a Galahad caller), which is
    what was periodically stalling the listener and forcing reconnect storms when
    the same process also had to decode + MJPEG-encode + write a preview frame.
    """
    args = ["ffmpeg", "-nostdin", "-loglevel", "warning"]
    args += build_input_args(spec)
    args += ["-c", "copy", "-f", "mpegts", relay_url]
    return args


def build_udp_preview_command(
    spec: "MediaInput", relay_port: int, preview_path: Path
) -> list[str]:
    """Preview generator that reads the local multicast relay (not the SRT socket).

    Decoding/scaling/MJPEG-encoding all happen here. If this process stalls or
    dies, only the preview is affected; the SRT receiver keeps the remote sender
    connected. ``overrun_nonfatal`` keeps the reader alive through bursts.
    """
    relay_in = relay_recv_url(relay_port)
    args = ["ffmpeg", "-nostdin", "-loglevel", "warning", "-fflags", "nobuffer", "-i", relay_in]
    args += [
        "-an",
        "-vf",
        f"fps={spec.fps},scale={spec.width}:-2,format=yuvj420p",
        "-threads",
        "1",
        "-q:v",
        str(spec.quality),
        "-update",
        "1",
        "-y",
        str(preview_path),
    ]
    return args


class FfmpegFrameTapSource:
    """Frame-tap source backed by JPEG files written by FFmpeg.

    Each active input has an FFmpeg process pulling its feed and overwriting a
    single JPEG (`-update 1`) at the configured fps/scale. `grab` returns frame
    metadata stamped with the file's modification time so the rest of the
    pipeline sees real capture timestamps.
    """

    def __init__(self, preview_dir: Path, load_pixels: bool = False) -> None:
        self.preview_dir = preview_dir
        self.load_pixels = load_pixels

    def grab(self, input_id: str) -> FrameTap | None:
        path = self.preview_dir / f"{input_id}.jpg"
        if not path.exists():
            return None
        captured_at = path.stat().st_mtime * 1000.0
        pixels = None
        width = 0
        height = 0
        if self.load_pixels:
            try:
                import cv2

                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            except Exception:
                image = None
            if image is not None:
                pixels = image
                height, width = image.shape[:2]
        return FrameTap(
            input_id=input_id,
            width=width,
            height=height,
            captured_at=captured_at,
            pixels=pixels,
            metadata={"path": str(path), "source": "ffmpeg"},
        )


class MediaIngestManager:
    """Runs/supervises FFmpeg workers and reflects real signal into the matrix.

    On each `poll`, an input whose preview frame is fresh becomes ACTIVE; an
    input whose frame is missing or stale becomes MISSING_SIGNAL, which makes the
    Routing Engine refuse to route to it (source-loss handling).
    """

    def __init__(
        self,
        matrix: MatrixCore,
        inputs: list[MediaInput],
        preview_dir: str | os.PathLike[str] = ".preview",
        freshness_ms: float = 2000.0,
        restart_backoff_ms: float = 2000.0,
        srt_restart_backoff_ms: float = 250.0,
        load_pixels: bool = False,
    ) -> None:
        self.matrix = matrix
        self.preview_dir = Path(preview_dir)
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self.freshness_ms = freshness_ms
        self.restart_backoff_ms = restart_backoff_ms
        # An SRT input is a live socket shared with a remote peer (e.g. a Galahad
        # caller pushing into our listener). When such a worker dies, the remote
        # peer's connection is already torn down and it begins its own reconnect.
        # If we wait the full file-oriented backoff before re-listening, there is
        # a dead window where the peer cannot connect, and the two reconnect loops
        # lock into anti-phase -- the feed flaps forever. A freshly spawned ffmpeg
        # listener simply blocks waiting for a caller without dying, so re-binding
        # almost immediately keeps a stable endpoint ready for the peer to land on.
        self.srt_restart_backoff_ms = srt_restart_backoff_ms
        self.workers: dict[str, _Worker] = {
            spec.input_id: _Worker(spec=spec, preview_path=self.preview_dir / f"{spec.input_id}.jpg")
            for spec in inputs
        }
        self.source = FfmpegFrameTapSource(self.preview_dir, load_pixels=load_pixels)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self.workers and not ffmpeg_available():
            raise RuntimeError("ffmpeg not found on PATH; cannot start media ingest.")
        for worker in self.workers.values():
            self._spawn(worker)
        self._thread = threading.Thread(target=self._loop, name="media-ingest", daemon=True)
        self._thread.start()
        logger.info("Media ingest started for %d input(s).", len(self.workers))

    def stop(self) -> None:
        self._stop.set()
        for worker in self.workers.values():
            self._kill(worker)

    def set_input(self, spec: MediaInput) -> None:
        """Add or update a feed at runtime and (re)spawn its FFmpeg worker."""
        if not ffmpeg_available():
            raise RuntimeError("ffmpeg not found on PATH; cannot start media ingest.")
        worker = self.workers.get(spec.input_id)
        if worker is None:
            worker = _Worker(
                spec=spec, preview_path=self.preview_dir / f"{spec.input_id}.jpg"
            )
            self.workers[spec.input_id] = worker
        else:
            worker.spec = spec
        self._spawn(worker)
        logger.info("media input %s configured -> %s", spec.input_id, spec.read_url)

    def remove_input(self, input_id: str) -> None:
        """Stop and forget a feed; mark its slot inactive."""
        worker = self.workers.pop(input_id, None)
        if worker is not None:
            self._kill(worker)
        slot = self.matrix.inputs.get(input_id)
        if slot is not None:
            slot.state = SlotState.INACTIVE
            slot.uri = None
        try:
            (self.preview_dir / f"{input_id}.jpg").unlink(missing_ok=True)
        except OSError:  # pragma: no cover - defensive
            pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(0.25)

    # -- per-tick work -------------------------------------------------------
    def poll(self) -> None:
        now = now_ms()
        for worker in self.workers.values():
            self._supervise(worker, now)
            self._reflect_signal(worker, now)

    def _reflect_signal(self, worker: _Worker, now: float) -> None:
        slot_id = worker.spec.input_id
        frame = self.source.grab(slot_id)
        fresh = frame is not None and (now - frame.captured_at) <= self.freshness_ms
        slot = self.matrix.inputs.get(slot_id)
        if slot is None:
            return
        if fresh:
            if slot.state != SlotState.ACTIVE:
                logger.info("input %s signal acquired", slot_id)
            slot.state = SlotState.ACTIVE
            self.matrix.mark_input_frame(slot_id, frame.captured_at)
        else:
            if slot.state == SlotState.ACTIVE:
                logger.warning("input %s signal lost", slot_id)
            slot.state = SlotState.MISSING_SIGNAL
            slot.last_frame_at = None

    def _supervise(self, worker: _Worker, now: float) -> None:
        if self._stop.is_set():
            return
        if worker.spec.source_type == "srt":
            self._supervise_srt(worker, now)
            return
        if worker.process is not None and worker.process.poll() is None:
            return
        if now - worker.last_started_at < self._backoff_for(worker.spec):
            return
        self._spawn(worker)

    def _supervise_srt(self, worker: _Worker, now: float) -> None:
        """Keep the SRT receiver and its preview alive *independently*.

        Because the relay is multicast, neither process depends on the other
        being up: a preview restart never disturbs the receiver, and a receiver
        re-listen never tears down a healthy preview. We therefore only ever
        respawn whichever process has actually died.
        """
        recv_dead = worker.process is None or worker.process.poll() is not None
        if recv_dead and now - worker.last_started_at >= self.srt_restart_backoff_ms:
            # The live socket is gone (sender disconnected, or receiver exited);
            # re-listen fast so the peer lands on a ready endpoint. Leave the
            # preview alone -- it will resume the moment packets flow again.
            self._spawn_receiver(worker)
        preview_dead = (
            worker.preview_process is None or worker.preview_process.poll() is not None
        )
        if preview_dead and now - worker.preview_last_started_at >= self.srt_restart_backoff_ms:
            self._spawn_preview(worker)

    def _backoff_for(self, spec: MediaInput) -> float:
        """Respawn delay for a worker; SRT feeds re-listen fast to avoid flapping."""
        if spec.source_type == "srt":
            return self.srt_restart_backoff_ms
        return self.restart_backoff_ms

    def _spawn(self, worker: _Worker) -> None:
        if worker.spec.source_type == "srt":
            self._spawn_srt(worker)
            return
        self._kill(worker)
        worker.last_started_at = now_ms()
        worker.process = self._popen(self._command(worker.spec), worker.spec.input_id)
        logger.info("ffmpeg ingest spawned for %s <- %s", worker.spec.input_id, worker.spec.read_url)

    def _spawn_srt(self, worker: _Worker) -> None:
        """(Re)start the decoupled SRT receiver + preview pair from scratch."""
        self._kill(worker)
        worker.relay_port = _free_udp_port()
        # Start the preview first so the relay group has a listener, then the
        # receiver. After this both are supervised independently.
        self._spawn_preview(worker)
        self._spawn_receiver(worker)

    def _spawn_receiver(self, worker: _Worker) -> None:
        if worker.relay_port is None:
            worker.relay_port = _free_udp_port()
        self._kill_process(worker, "process")
        worker.last_started_at = now_ms()
        worker.process = self._popen(
            build_srt_receiver_command(worker.spec, relay_send_url(worker.relay_port)),
            worker.spec.input_id,
            suffix="recv",
        )
        logger.info(
            "ffmpeg srt receiver spawned for %s <- %s (relay %s:%d)",
            worker.spec.input_id,
            worker.spec.read_url,
            RELAY_MULTICAST_GROUP,
            worker.relay_port,
        )

    def _spawn_preview(self, worker: _Worker) -> None:
        if worker.relay_port is None:
            worker.relay_port = _free_udp_port()
        self._kill_process(worker, "preview_process")
        worker.preview_last_started_at = now_ms()
        worker.preview_process = self._popen(
            build_udp_preview_command(worker.spec, worker.relay_port, worker.preview_path),
            worker.spec.input_id,
            suffix="preview",
        )

    def _popen(
        self, command: list[str], input_id: str, suffix: str | None = None
    ) -> subprocess.Popen:
        name = f"ingest_{input_id}.log" if suffix is None else f"ingest_{input_id}_{suffix}.log"
        stderr_target: object = subprocess.DEVNULL
        try:
            stderr_target = open(self.preview_dir / name, "a", encoding="utf-8")
            stderr_target.write(f"\n=== spawn {now_ms():.0f} ===\n")
            stderr_target.flush()
        except OSError:  # pragma: no cover - fall back to discarding stderr
            stderr_target = subprocess.DEVNULL
        proc = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=stderr_target,
            stdin=subprocess.DEVNULL,
        )
        if stderr_target is not subprocess.DEVNULL:
            stderr_target.close()  # type: ignore[union-attr]
        return proc

    def _kill(self, worker: _Worker) -> None:
        self._kill_process(worker, "process")
        self._kill_process(worker, "preview_process")

    def _kill_process(self, worker: _Worker, attr: str) -> None:
        proc: subprocess.Popen | None = getattr(worker, attr)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                proc.kill()
        setattr(worker, attr, None)

    def _command(self, spec: MediaInput) -> list[str]:
        return build_ingest_command(spec, self.preview_dir / f"{spec.input_id}.jpg")

    # -- read side -----------------------------------------------------------
    def latest_jpeg(self, input_id: str) -> bytes | None:
        path = self.preview_dir / f"{input_id}.jpg"
        if not path.exists():
            return None
        try:
            if (now_ms() - (path.stat().st_mtime * 1000.0)) > self.freshness_ms:
                path.unlink(missing_ok=True)
                return None
            return path.read_bytes()
        except OSError:  # pragma: no cover - file mid-write
            return None
