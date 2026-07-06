from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading

from excalibur.matrix.clock import now_ms
from excalibur.matrix.media.ingest import ffmpeg_available

logger = logging.getLogger(__name__)

# akvirtualcamera's Windows installer drops the manager here (not on PATH), so we
# probe these locations when the bare name can't be resolved from PATH.
_WINDOWS_MANAGER_DIRS = (
    r"C:\Program Files\akvirtualcamera\x64",
    r"C:\Program Files\akvirtualcamera\x86",
    r"C:\Program Files (x86)\akvirtualcamera\x64",
    r"C:\Program Files (x86)\akvirtualcamera\x86",
)


def resolve_manager_bin(manager_bin: str) -> str | None:
    """Resolve the akvirtualcamera manager to a runnable path, or None.

    Tries PATH first (so an explicit absolute path or a PATH entry wins), then
    falls back to the known Windows install locations since the installer does
    not add itself to PATH.
    """
    found = shutil.which(manager_bin)
    if found:
        return found
    # An explicit existing path that shutil.which didn't accept (e.g. missing
    # extension handling) still counts.
    if os.path.isfile(manager_bin):
        return manager_bin
    name = os.path.basename(manager_bin)
    if not name.lower().endswith(".exe"):
        name = f"{name}.exe"
    for directory in _WINDOWS_MANAGER_DIRS:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            return candidate
    return None

# Default Windows virtual-camera identity. ``akvirtualcamera`` (the open-source
# DirectShow virtual camera by the Webcamoid project) registers a device whose
# *description* is what shows up in OBS' "Video Capture Device" list. We register
# and feed a device described/identified as "Excalibur" so the operator can pick
# Excalibur as a Device instead of (or alongside) the SRT Media Source.
DEFAULT_DEVICE_NAME = "Excalibur"
# akvirtualcamera ships a CLI manager (``AkVCamManager``). It reads raw frames on
# stdin and pushes them into the virtual device. We pipe FFmpeg's decoded output
# straight into it.
DEFAULT_MANAGER_BIN = "AkVCamManager"
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30
# akvirtualcamera accepts raw RGB24 frames; FFmpeg emits the same pixel format.
VCAM_PIXEL_FORMAT = "RGB24"


def build_decode_command(
    read_url: str,
    width: int,
    height: int,
    fps: int,
    source_type: str = "srt",
    loop: bool = False,
) -> list[str]:
    """FFmpeg command that decodes the routed program feed into raw RGB24 frames.

    The frames are written to stdout so they can be piped into the virtual-camera
    manager. Audio is dropped (``-an``) because a Windows video capture device
    only carries video; broadcast audio still flows through OBS as before.
    """
    args = ["ffmpeg", "-nostdin", "-loglevel", "warning"]
    if source_type == "file":
        if loop:
            args += ["-stream_loop", "-1"]
        args += ["-re"]
    else:
        if read_url.startswith("rtsp://") or source_type == "rtsp":
            args += ["-rtsp_transport", "tcp"]
        args += ["-fflags", "nobuffer"]
    args += ["-i", read_url]
    args += [
        "-an",
        "-vf",
        f"scale={width}:{height},fps={fps},format=rgb24",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    return args


def build_vcam_stream_command(
    manager_bin: str,
    device_name: str,
    width: int,
    height: int,
    fps: int,
) -> list[str]:
    """akvirtualcamera command that reads raw RGB24 frames from stdin.

    ``AkVCamManager stream <device> <format> <width> <height>`` consumes raw
    frames on stdin and feeds them to the registered virtual device.
    """
    return [
        manager_bin,
        "stream",
        "--fps",
        str(fps),
        device_name,
        VCAM_PIXEL_FORMAT,
        str(width),
        str(height),
    ]


def vcam_setup_commands(
    device_name: str,
    width: int,
    height: int,
    fps: int,
    manager_bin: str = DEFAULT_MANAGER_BIN,
) -> list[str]:
    """One-time registration commands the operator runs as Administrator.

    Returned as display strings so the UI/docs can show exactly what to run after
    installing akvirtualcamera. Registration touches the system DirectShow
    registry, which requires elevation, so it is intentionally not run by the
    backend itself.
    """
    return [
        f'{manager_bin} add-device "{device_name}" --id {device_name}',
        f"{manager_bin} add-format {device_name} {VCAM_PIXEL_FORMAT} "
        f"{width} {height} {fps}",
        f"{manager_bin} update",
    ]


class VirtualCamOutputManager:
    """Mirrors the routed program feed into a Windows virtual-camera device.

    This is a sibling of :class:`ProgramOutputManager`: it follows the same
    routed input, but instead of republishing SRT for an OBS *Media Source* it
    decodes the feed and pipes it into akvirtualcamera so OBS can pick Excalibur
    from the *Video Capture Device* list. The two paths are independent; an
    operator may run either, both, or neither.

    Like the program publisher it tracks the intended source even while disabled
    (so the UI can report it) and only spawns processes once enabled, which the
    service ties to real media ingest existing.
    """

    def __init__(
        self,
        device_name: str = DEFAULT_DEVICE_NAME,
        manager_bin: str = DEFAULT_MANAGER_BIN,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        fps: int = DEFAULT_FPS,
        restart_backoff_ms: float = 2000.0,
    ) -> None:
        self.device_name = device_name
        self.manager_bin = manager_bin
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.restart_backoff_ms = restart_backoff_ms
        self.active = False
        self.current_input: str | None = None
        self.current_read_url: str | None = None
        self.current_source_type: str = "srt"
        self.current_loop: bool = False
        self._ffmpeg: subprocess.Popen | None = None
        self._vcam: subprocess.Popen | None = None
        self._last_started_at = 0.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------
    def enable(self) -> None:
        """Start feeding the device. Until enabled, the source is only tracked."""
        with self._lock:
            if self.active:
                return
            self.active = True
            self._stop.clear()
            if self.current_read_url:
                self._try_spawn()
            self._thread = threading.Thread(
                target=self._loop, name="virtualcam-output", daemon=True
            )
            self._thread.start()
        logger.info(
            "Virtual camera output enabled -> device %r", self.device_name
        )

    def set_source(
        self,
        input_id: str | None,
        read_url: str | None,
        source_type: str = "srt",
        loop: bool = False,
    ) -> None:
        """Point the virtual camera at a new input feed (route change)."""
        with self._lock:
            changed = (
                read_url != self.current_read_url
                or source_type != self.current_source_type
                or loop != self.current_loop
            )
            self.current_input = input_id
            self.current_read_url = read_url
            self.current_source_type = source_type
            self.current_loop = loop
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
            return (
                self._ffmpeg is not None
                and self._ffmpeg.poll() is None
                and self._vcam is not None
                and self._vcam.poll() is None
            )

    def resolved_manager_bin(self) -> str | None:
        return resolve_manager_bin(self.manager_bin)

    def manager_available(self) -> bool:
        return self.resolved_manager_bin() is not None

    def setup_commands(self) -> list[str]:
        return vcam_setup_commands(
            self.device_name,
            self.width,
            self.height,
            self.fps,
            manager_bin=self.manager_bin,
        )

    def status(self) -> dict:
        return {
            "device_name": self.device_name,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "source_input": self.current_input,
            "source_url": self.current_read_url,
            "running": self.is_running(),
            "active": self.active,
            "manager_available": self.manager_available(),
            "setup_commands": self.setup_commands(),
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
            if self.is_running():
                return
            if now_ms() - self._last_started_at < self.restart_backoff_ms:
                return
            self._try_spawn()

    def _try_spawn(self) -> bool:
        if not self.current_read_url:
            return False
        if not ffmpeg_available():
            logger.error("ffmpeg not found on PATH; cannot feed virtual camera.")
            return False
        manager = self.resolved_manager_bin()
        if manager is None:
            logger.error(
                "%s not found on PATH or in the akvirtualcamera install dir; "
                "install akvirtualcamera and register the %r device before "
                "enabling the virtual camera output.",
                self.manager_bin,
                self.device_name,
            )
            return False
        self._kill()
        self._last_started_at = now_ms()
        decode_cmd = build_decode_command(
            self.current_read_url,
            self.width,
            self.height,
            self.fps,
            source_type=self.current_source_type,
            loop=self.current_loop,
        )
        stream_cmd = build_vcam_stream_command(
            manager, self.device_name, self.width, self.height, self.fps
        )
        self._ffmpeg = subprocess.Popen(
            decode_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        self._vcam = subprocess.Popen(
            stream_cmd,
            stdin=self._ffmpeg.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Let the vcam process own the read end so FFmpeg sees SIGPIPE if it dies.
        if self._ffmpeg.stdout is not None:
            self._ffmpeg.stdout.close()
        logger.info(
            "virtual camera pipeline spawned: %s -> %s (device %r)",
            self.current_read_url,
            self.manager_bin,
            self.device_name,
        )
        return True

    def _kill(self) -> None:
        for proc in (self._vcam, self._ffmpeg):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                    proc.kill()
        self._vcam = None
        self._ffmpeg = None
