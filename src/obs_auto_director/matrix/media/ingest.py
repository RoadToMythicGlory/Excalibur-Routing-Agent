from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from obs_auto_director.matrix.clock import now_ms
from obs_auto_director.matrix.domain import SlotState
from obs_auto_director.matrix.frame_tap import FrameTap
from obs_auto_director.matrix.matrix_core import MatrixCore

logger = logging.getLogger(__name__)


@dataclass
class MediaInput:
    """One real feed: how to read it and where to write its preview frame."""

    input_id: str
    read_url: str
    width: int = 480
    fps: float = 5.0
    quality: int = 7  # ffmpeg -q:v, lower is better


@dataclass
class _Worker:
    spec: MediaInput
    preview_path: Path
    process: subprocess.Popen | None = None
    last_started_at: float = 0.0


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


class FfmpegFrameTapSource:
    """Frame-tap source backed by JPEG files written by FFmpeg.

    Each active input has an FFmpeg process pulling its feed and overwriting a
    single JPEG (`-update 1`) at the configured fps/scale. `grab` returns frame
    metadata stamped with the file's modification time so the rest of the
    pipeline sees real capture timestamps.
    """

    def __init__(self, preview_dir: Path) -> None:
        self.preview_dir = preview_dir

    def grab(self, input_id: str) -> FrameTap | None:
        path = self.preview_dir / f"{input_id}.jpg"
        if not path.exists():
            return None
        captured_at = path.stat().st_mtime * 1000.0
        return FrameTap(
            input_id=input_id,
            width=0,
            height=0,
            captured_at=captured_at,
            pixels=None,
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
    ) -> None:
        self.matrix = matrix
        self.preview_dir = Path(preview_dir)
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self.freshness_ms = freshness_ms
        self.restart_backoff_ms = restart_backoff_ms
        self.workers: dict[str, _Worker] = {
            spec.input_id: _Worker(spec=spec, preview_path=self.preview_dir / f"{spec.input_id}.jpg")
            for spec in inputs
        }
        self.source = FfmpegFrameTapSource(self.preview_dir)
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
        if worker.process is not None and worker.process.poll() is None:
            return
        if now - worker.last_started_at < self.restart_backoff_ms:
            return
        self._spawn(worker)

    def _spawn(self, worker: _Worker) -> None:
        self._kill(worker)
        worker.last_started_at = now_ms()
        worker.process = subprocess.Popen(
            self._command(worker.spec),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        logger.info("ffmpeg ingest spawned for %s <- %s", worker.spec.input_id, worker.spec.read_url)

    def _kill(self, worker: _Worker) -> None:
        if worker.process is not None and worker.process.poll() is None:
            worker.process.terminate()
            try:
                worker.process.wait(timeout=3)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                worker.process.kill()
        worker.process = None

    def _command(self, spec: MediaInput) -> list[str]:
        url = spec.read_url
        args = ["ffmpeg", "-nostdin", "-loglevel", "warning"]
        if url.startswith("rtsp://"):
            args += ["-rtsp_transport", "tcp"]
        args += [
            "-fflags",
            "nobuffer",
            "-i",
            url,
            "-an",
            "-vf",
            f"fps={spec.fps},scale={spec.width}:-2",
            "-q:v",
            str(spec.quality),
            "-update",
            "1",
            "-y",
            str(self.preview_dir / f"{spec.input_id}.jpg"),
        ]
        return args

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
