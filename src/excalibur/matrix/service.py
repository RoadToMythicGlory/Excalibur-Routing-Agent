from __future__ import annotations

import logging
import os
import string
import threading
from pathlib import Path

from excalibur.config import ObsConfig
from excalibur.cv.agent.vision_agent import SoccerVisionAgent
from excalibur.cv.inference.engine import load_manifest
from excalibur.cv.inference.onnx_runner import OnnxInferenceEngine
from excalibur.cv.profiles import resolve_runtime_profile
from excalibur.matrix.agents.director_agent import (
    DEFAULT_ROTATE_INTERVAL_MS,
    DirectorAgent,
)
from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import now_ms
from excalibur.matrix.config_store import MatrixConfigStore
from excalibur.matrix.domain import (
    OutputKind,
    SlotState,
    input_slot_id,
    output_slot_id,
)
from excalibur.matrix.frame_tap import FrameTapManager, SimulatedFrameTapSource
from excalibur.matrix.media.ingest import (
    MediaIngestManager,
    MediaInput,
    build_srt_url,
)
from excalibur.matrix.config_store import BACKEND_GSTREAMER
from excalibur.matrix.media.compositor import CompositorOutputManager
from excalibur.matrix.media.gstreamer_output import GstProgramOutputManager
from excalibur.matrix.media.output import (
    DEFAULT_PROGRAM_PUBLISH_URL,
    program_obs_url,
)
from excalibur.matrix.media.virtualcam import VirtualCamOutputManager
from excalibur.matrix.gsi.receiver import GsiReceiver
from excalibur.matrix.gsi.simulator import ROSTER
from excalibur.matrix.intelligence.engine import MatchIntelligenceEngine
from excalibur.matrix.soccer.camera_map import SoccerCameraMap
from excalibur.matrix.soccer.config import load_soccer_profile
from excalibur.matrix.soccer.director import SoccerDirector
from excalibur.matrix.soccer.domain import DirectorMode
from excalibur.matrix.soccer.fusion import SoccerFusion
from excalibur.matrix.soccer.receiver import SoccerReceiver
from excalibur.matrix.latency import LatencyBudget, LatencyTracker
from excalibur.matrix.matrix_core import MatrixCore
from excalibur.matrix.net import local_ip_addresses, primary_ip, public_ip
from excalibur.matrix.obs_bridge import ObsBridge
from excalibur.matrix.routing_engine import RoutingEngine
from excalibur.obs.client import ObsSceneRouter

logger = logging.getLogger(__name__)

# Video containers an operator might point a file/VOD input at.
MEDIA_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".ts",
    ".m4v",
    ".webm",
    ".flv",
    ".m2ts",
    ".mpg",
    ".mpeg",
    ".wmv",
}


def _filesystem_roots() -> list[dict]:
    """Top-level entries: Windows drives (or '/' on POSIX), plus handy shortcuts."""
    roots: list[dict] = []
    seen: set[str] = set()

    def add(name: str, path: str) -> None:
        if path and path not in seen and os.path.isdir(path):
            roots.append({"name": name, "path": path, "is_dir": True})
            seen.add(path)

    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            add(drive, drive)
    else:
        add("/", "/")
    add("Home", str(Path.home()))
    add("Working dir", str(Path.cwd()))
    return roots


def browse_filesystem(path: str | None) -> dict:
    """List directories and media files for the operator's file picker.

    With no path, returns filesystem roots. If `path` points at a file, its
    parent directory is listed (so re-opening a picked file lands beside it).
    Only directories and recognized video files are returned.
    """
    if not path:
        return {"path": "", "parent": None, "is_root": True, "entries": _filesystem_roots()}

    target = Path(path)
    if target.is_file():
        target = target.parent
    if not target.is_dir():
        raise FileNotFoundError(f"Not a directory: {path}")

    entries: list[dict] = []
    try:
        children = sorted(
            target.iterdir(), key=lambda c: (not _safe_is_dir(c), c.name.lower())
        )
    except (PermissionError, OSError) as error:
        raise FileNotFoundError(f"Cannot read directory: {path} ({error})") from error

    for child in children:
        is_dir = _safe_is_dir(child)
        if is_dir:
            entries.append({"name": child.name, "path": str(child), "is_dir": True})
        elif child.suffix.lower() in MEDIA_EXTENSIONS:
            entries.append({"name": child.name, "path": str(child), "is_dir": False})

    parent = str(target.parent) if target.parent != target else None
    return {"path": str(target), "parent": parent, "is_root": False, "entries": entries}


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:  # pragma: no cover - permission/IO races
        return False


def _as_bool(value: object) -> bool:
    """Coerce persisted JSON/string values into a bool (handles "true"/"on"/1)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


class MatrixService:
    """Wires the full bus-driven slice on an always-11x11 matrix.

    Data flow: GSI -> Game State Bus -> Match Intelligence -> Intelligence Bus
    -> Director Agent -> Decision Bus -> Routing Engine -> Matrix + OBS. The
    Routing Engine is the only writer of route state.
    """

    def __init__(
        self,
        active_inputs: int = 3,
        active_outputs: int = 3,
        budget: LatencyBudget | None = None,
        obs_dry_run: bool = True,
        program_output: str = "output_01",
        config_path: str = "configs/matrix.runtime.json",
        input_count: int | None = None,
        output_count: int | None = None,
        sport_mode: str | None = None,
    ) -> None:
        self.budget = budget or LatencyBudget()
        self.latency = LatencyTracker(self.budget)
        self.config_store = MatrixConfigStore(config_path)
        self.program_output = self.config_store.program_output or program_output

        # Slot counts come from runtime config (default 11/11) unless explicitly
        # overridden by the caller.
        self.input_count = int(input_count or self.config_store.input_count)
        self.output_count = int(output_count or self.config_store.output_count)
        self.matrix = MatrixCore(self.input_count, self.output_count)
        self.buses = MatrixBuses()

        self.steam_to_input = {
            steamid: input_slot_id(index + 1) for index, (steamid, _, _) in enumerate(ROSTER)
        }
        self._configure_slots(active_inputs, active_outputs)

        self.gsi = GsiReceiver(
            self.buses, budget=self.budget, steam_to_input=self.steam_to_input
        )
        self.intelligence = MatchIntelligenceEngine(self.buses)
        if sport_mode:
            self.config_store.set_sport_mode(sport_mode)
        self.sport_mode = self.config_store.sport_mode
        self.main_input = self.config_store.main_input
        self.cv_profile_name = self.config_store.cv_profile
        self.director_mode = DirectorMode(self.config_store.director_mode)
        self.suspension = self.config_store.suspension
        self._cv_runtime = None
        self.soccer_profile = None
        self.soccer = None
        self.soccer_fusion = None
        self.soccer_cameras = None
        self.soccer_director = None
        self.vision_agent = None
        cs2_enabled = (
            self.sport_mode != "soccer" and self.director_mode == DirectorMode.AUTOMATIC
        )
        self.director = DirectorAgent(
            self.buses,
            self.matrix,
            program_output=program_output,
            steam_to_input=self.steam_to_input,
            enabled=cs2_enabled,
            state_provider=lambda: self.gsi.state,
        )
        if self.sport_mode == "soccer":
            self._init_soccer()

        # Background ticker that drives the director's idle rotation so a quiet
        # match keeps cycling across live inputs instead of sitting on one feed.
        # The interval doubles as the per-feed dwell time during idle rotation.
        self._director_stop = threading.Event()
        self._director_thread: threading.Thread | None = None
        self.rotate_interval_ms = DEFAULT_ROTATE_INTERVAL_MS

        self.frame_source = SimulatedFrameTapSource()
        self.frames = FrameTapManager(
            self.matrix, self.frame_source, budget=self.budget, fps=self._frame_tap_fps()
        )
        self.media: MediaIngestManager | None = None
        self._media_preview_dir = ".preview"
        self._media_freshness_ms = 2000.0

        self.input_scene_map = {
            input_slot_id(index): f"PGM - Player {index:02d}"
            for index in range(1, self.input_count + 1)
        }
        self.obs = ObsBridge(
            ObsSceneRouter(ObsConfig(dry_run=obs_dry_run)),
            program_output=program_output,
            input_scene_map=self.input_scene_map,
            output_scene_map={program_output: "PGM - Program"},
        )

        # Excalibur-owned broadcast program feed. OBS reads this single stable
        # source; the routing engine swaps the content behind it. This is the
        # program/quality path, kept separate from the agent's frame taps.
        self.program_publish_url = (
            self.config_store.program_publish_url or DEFAULT_PROGRAM_PUBLISH_URL
        )
        self.program_output_backend = self.config_store.program_output_backend
        self.program = self._build_program_manager()
        self.matrix.configure_output(self.program_output, uri=self.program_publish_url)

        # Optional second program path: expose the routed program feed as a
        # Windows virtual-camera device (akvirtualcamera) so OBS can select
        # "Excalibur" from the Video Capture Device list. Follows the same routed
        # input as the SRT program output; the two run independently.
        vcam_cfg = self.config_store.virtualcam
        self.virtualcam = VirtualCamOutputManager(
            device_name=vcam_cfg["device"],
            manager_bin=vcam_cfg["manager_bin"],
            width=vcam_cfg["width"],
            height=vcam_cfg["height"],
            fps=vcam_cfg["fps"],
        )
        self.virtualcam_enabled = bool(vcam_cfg["enabled"])

        self.routing = RoutingEngine(
            self.matrix,
            self.buses,
            budget=self.budget,
            on_route_applied=self._on_route_applied,
        )

        self._apply_config_store()

    def _init_soccer(self) -> None:
        profile_path = self.config_store.game_profile or "configs/games/soccer.json"
        self.soccer_profile = load_soccer_profile(profile_path)
        self.main_input = self.soccer_profile.main_input
        self.soccer = SoccerReceiver(self.buses, self.soccer_profile)
        self.soccer_fusion = SoccerFusion(self.buses, self.soccer_profile)
        self.soccer_cameras = SoccerCameraMap(self.soccer_profile)
        self.soccer_director = SoccerDirector(
            self.buses,
            self.matrix,
            self.soccer_fusion,
            self.soccer_cameras,
            self.soccer_profile,
        )
        self.soccer_director.set_mode(self.director_mode)
        runtime = resolve_runtime_profile(self.soccer_profile, self.cv_profile_name)
        self._cv_runtime = runtime
        manifest = load_manifest(self.soccer_profile.inference.model_manifest)
        engine = None
        if manifest is not None and manifest.path:
            # A deployed model manifest gets the real ONNX backend; without one
            # the deterministic mock keeps the pipeline alive in shadow mode.
            engine = OnnxInferenceEngine(manifest, runtime.provider)
        self.vision_agent = SoccerVisionAgent(
            self.soccer_fusion,
            self.soccer_profile,
            runtime,
            engine=engine,
            manifest=manifest,
            latency=self.latency,
            budget=self.budget,
        )
        if self.soccer_fusion is not None:
            self.soccer_fusion.model_version = (
                manifest.version if manifest is not None else None
            )
        logger.info(
            "Soccer director ready profile=%s mode=%s cv=%s",
            self.soccer_profile.version,
            self.director_mode.value,
            runtime.name,
        )

    def _configure_slots(self, active_inputs: int, active_outputs: int) -> None:
        for index in range(1, self.input_count + 1):
            slot_id = input_slot_id(index)
            if index <= active_inputs:
                self.matrix.configure_input(
                    slot_id,
                    state=SlotState.ACTIVE,
                    protocol="srt",
                    uri=f"srt://0.0.0.0:{10000 + index}",
                )
            else:
                self.matrix.configure_input(slot_id, state=SlotState.INACTIVE)

        output_kinds = {
            1: OutputKind.PROGRAM,
            2: OutputKind.PREVIEW,
            3: OutputKind.MULTIVIEW,
        }
        for index in range(1, self.output_count + 1):
            slot_id = output_slot_id(index)
            kind = output_kinds.get(index, OutputKind.RESERVED)
            state = SlotState.ACTIVE if index <= active_outputs else SlotState.RESERVED
            self.matrix.configure_output(slot_id, kind=kind, state=state)

    def _build_program_manager(self):
        """Instantiate the configured program-output backend.

        Both backends expose the same interface (``enable``/``stop``/
        ``set_inputs``/``set_active``/``is_running``/``status`` plus the
        ``publish_url``/``current_input``/``active`` attributes), so the rest of
        the service is backend-agnostic. The FFmpeg compositor remains the
        default; the GStreamer media-core path is opt-in via config.
        """
        if self.program_output_backend == BACKEND_GSTREAMER:
            settings = self.config_store.program_output_settings
            logger.info(
                "Program output backend: GStreamer media-core (%dx%d@%d, %s)",
                settings["width"],
                settings["height"],
                settings["fps"],
                settings["encoder"],
            )
            return GstProgramOutputManager(
                self.program_publish_url,
                width=settings["width"],
                height=settings["height"],
                fps=settings["fps"],
                pixel_format=settings["pixel_format"],
                encoder=settings["encoder"],
                video_bitrate_kbps=settings["video_bitrate_kbps"],
            )
        logger.info("Program output backend: FFmpeg compositor")
        return CompositorOutputManager(self.program_publish_url)

    def _apply_config_store(self) -> None:
        for input_id, fields in self.config_store.all_inputs().items():
            self._apply_input_fields(input_id, fields)

    def _apply_input_fields(self, input_id: str, fields: dict) -> None:
        if input_id not in self.matrix.inputs:
            return
        slot_fields = {
            key: value
            for key, value in fields.items()
            if key in ("player_name", "team", "role")
        }
        if slot_fields:
            self.matrix.configure_input(input_id, **slot_fields)
        scene = fields.get("obs_scene")
        if scene:
            self.obs.input_scene_map[input_id] = scene
        self._apply_source_fields(input_id, fields)
        self._sync_compositor_inputs()

    def _resolve_source_mode(self, fields: dict) -> str:
        """Normalize the configured source mode, mapping legacy srt_mode values."""
        mode = (fields.get("source_mode") or "").strip().lower()
        if mode:
            return mode
        srt = (fields.get("srt_mode") or "").strip().lower()
        if srt == "listener":
            return "srt_listener"
        if srt == "caller":
            return "srt_caller"
        return "off"

    def _build_media_input(
        self, input_id: str, mode: str, fields: dict
    ) -> tuple[MediaInput | None, str | None]:
        """Build a MediaInput + slot protocol for a configured source, or (None, None)."""
        if mode in ("srt_listener", "srt_caller"):
            port = fields.get("srt_port")
            if not port:
                return None, None
            srt_mode = "caller" if mode == "srt_caller" else "listener"
            url = build_srt_url(
                srt_mode,
                fields.get("srt_host"),
                int(port),
                int(fields.get("srt_latency") or 200),
            )
            return (
                MediaInput(
                    input_id=input_id,
                    read_url=url,
                    source_type="srt",
                    width=self._ingest_width(),
                ),
                "srt",
            )
        if mode in ("rtsp", "url"):
            url = (fields.get("source_url") or "").strip()
            if not url:
                return None, None
            return (
                MediaInput(
                    input_id=input_id,
                    read_url=url,
                    source_type=mode,
                    width=self._ingest_width(),
                ),
                mode,
            )
        if mode == "file":
            path = (fields.get("file_path") or "").strip()
            if not path:
                return None, None
            return (
                MediaInput(
                    input_id=input_id,
                    read_url=path,
                    source_type="file",
                    loop=_as_bool(fields.get("loop")),
                    width=self._ingest_width(),
                ),
                "file",
            )
        return None, None

    def _apply_source_fields(self, input_id: str, fields: dict) -> None:
        mode = self._resolve_source_mode(fields)
        if mode in ("off", "", "none"):
            # Only tear down a live feed when real ingest is running; in simulate
            # mode (no media) leave the slot in its configured state.
            if self.media is not None:
                self.media.remove_input(input_id)
            return
        spec, protocol = self._build_media_input(input_id, mode, fields)
        if spec is None:
            return
        self._ensure_media()
        self.matrix.configure_input(
            input_id, state=SlotState.MISSING_SIGNAL, protocol=protocol, uri=spec.read_url
        )
        assert self.media is not None
        self.media.set_input(spec)

    def _source_meta(self, input_id: str) -> tuple[str, bool]:
        """Return (source_type, loop) for an input from persisted config."""
        fields = self.config_store.input_config(input_id)
        mode = self._resolve_source_mode(fields)
        if mode == "file":
            return "file", _as_bool(fields.get("loop"))
        if mode in ("rtsp", "url"):
            return mode, False
        return "srt", False

    def _on_route_applied(self, route, decision) -> None:
        """Fan out an applied route to OBS layout switching and the program feed."""
        self.obs.on_route_applied(route, decision)
        self._update_program_output(route)
        if (
            self.sport_mode == "soccer"
            and self.soccer_profile is not None
            and self.soccer_profile.manual_suspension.on_operator_route
            and str(getattr(decision, "producer", "")).startswith("operator:")
            and decision.reason in ("manual_cut", "take_next", "emergency_route")
        ):
            self.suspend_automation(
                operator=str(decision.producer), reason=decision.reason
            )

    def _update_program_output(self, route) -> None:
        """Point the Excalibur program output at the input now routed to program.

        The compositor keeps every routable input decoded, so a cut is a seamless
        ``streamselect`` switch (``set_active``) rather than an FFmpeg respawn. The
        virtual-camera output still uses the legacy respawn path.
        """
        if route.output_id != self.program_output:
            return
        read_url = None
        source_type = "srt"
        loop = False
        if route.input_id is not None:
            slot = self.matrix.inputs.get(route.input_id)
            read_url = slot.uri if slot is not None else None
            source_type, loop = self._source_meta(route.input_id)
        self.program.set_active(route.input_id, read_url)
        self.virtualcam.set_source(
            route.input_id, read_url, source_type=source_type, loop=loop
        )

    def _sync_compositor_inputs(self) -> None:
        """Push the full set of routable inputs into the persistent compositor.

        The compositor graph contains one decoded pad per configured (non-``off``)
        input, in stable slot order, so ``set_active`` can map any of them on air
        without a respawn. This rebuilds the graph only when membership/URLs
        actually change.

        SRT sources are deliberately excluded. An SRT URL backs a single socket:
        a ``listener`` can be bound by only one process, and a ``caller`` receives
        the remote's single connection. The per-input ingest worker already opens
        that socket to produce the preview/signal frame, so if the compositor's
        FFmpeg also opens the same SRT URL it dies with EADDRINUSE (Windows
        ``-10048``) and crash-loops -- which takes down the *entire* program
        output, including the healthy file inputs, so nothing is served on the
        program SRT port at all. Until a local relay fans one SRT feed out to
        multiple readers, keep SRT inputs out of the compositor; routing one to
        program then shows the black slate instead of crashing the output.
        """
        specs: list[MediaInput] = []
        for input_id in self.matrix.inputs:
            fields = self.config_store.input_config(input_id)
            mode = self._resolve_source_mode(fields)
            if mode in ("off", "", "none"):
                continue
            if mode in ("srt_listener", "srt_caller"):
                continue
            spec, _protocol = self._build_media_input(input_id, mode, fields)
            if spec is not None:
                specs.append(spec)
        self.program.set_inputs(specs)

    def program_obs_url(self) -> str:
        """The URL an OBS media source should read to show the program feed."""
        return program_obs_url(self.program.publish_url, primary_ip())

    def _frame_tap_fps(self) -> float:
        """CV sample rate: the active inference profile's FPS in soccer mode."""
        if self.sport_mode == "soccer" and self._cv_runtime is not None:
            return float(self._cv_runtime.fps)
        return 5.0

    def _ingest_width(self) -> int:
        """Preview/CV decode width. Never below the 480px UI preview default,
        but wide enough that a GPU inference profile is not fed upscaled JPEGs."""
        if self.sport_mode == "soccer" and self._cv_runtime is not None:
            return max(480, int(self._cv_runtime.width))
        return 480

    def _ensure_media(self) -> None:
        if self.media is not None:
            return
        self.media = MediaIngestManager(
            self.matrix,
            [],
            preview_dir=self._media_preview_dir,
            freshness_ms=self._media_freshness_ms,
            load_pixels=self.sport_mode == "soccer",
        )
        self.frame_source = self.media.source
        self.frames = FrameTapManager(
            self.matrix, self.frame_source, budget=self.budget, fps=self._frame_tap_fps()
        )
        self.media.start()
        # Real feeds exist now, so the program publisher may spawn FFmpeg.
        self.program.enable()
        if self.virtualcam_enabled:
            self.virtualcam.enable()

    def update_input_config(self, input_id: str, fields: dict) -> dict:
        """Operator sets metadata for an input; persisted and applied live."""
        if input_id not in self.matrix.inputs:
            raise KeyError(f"Unknown input slot: {input_id}")
        saved = self.config_store.set_input(input_id, fields)
        self._apply_input_fields(input_id, saved)
        return saved

    def set_virtualcam_config(self, fields: dict) -> dict:
        """Operator toggles/configures the Excalibur virtual-camera device output.

        Settings persist and are applied live: enabling spins up the FFmpeg ->
        akvirtualcamera pipeline (once real media ingest exists), disabling tears
        it down. Geometry/device changes rebuild the manager so the next spawn
        uses them.
        """
        saved = self.config_store.set_virtualcam(fields)
        rebuild = (
            saved["device"] != self.virtualcam.device_name
            or saved["manager_bin"] != self.virtualcam.manager_bin
            or saved["width"] != self.virtualcam.width
            or saved["height"] != self.virtualcam.height
            or saved["fps"] != self.virtualcam.fps
        )
        if rebuild:
            was_active = self.virtualcam.active
            current_input = self.virtualcam.current_input
            current_url = self.virtualcam.current_read_url
            current_type = self.virtualcam.current_source_type
            current_loop = self.virtualcam.current_loop
            self.virtualcam.stop()
            self.virtualcam = VirtualCamOutputManager(
                device_name=saved["device"],
                manager_bin=saved["manager_bin"],
                width=saved["width"],
                height=saved["height"],
                fps=saved["fps"],
            )
            self.virtualcam.set_source(
                current_input, current_url, source_type=current_type, loop=current_loop
            )
            if was_active and saved["enabled"]:
                self.virtualcam.enable()

        self.virtualcam_enabled = bool(saved["enabled"])
        if self.virtualcam_enabled:
            # Only actually spawns once real media ingest is running.
            if self.media is not None:
                self.virtualcam.enable()
        else:
            self.virtualcam.stop()
        return self.virtualcam.status()

    def browse_media(self, path: str | None) -> dict:
        """List directories/media files on the backend machine for the picker."""
        return browse_filesystem(path)

    def set_matrix_size(
        self, input_count: int | None = None, output_count: int | None = None
    ) -> dict:
        """Grow or shrink the matrix. Only unused trailing slots may be removed.

        Raises ValueError if a requested removal would drop a routed/program slot.
        """
        if input_count is not None:
            self._resize_inputs(int(input_count))
        if output_count is not None:
            self._resize_outputs(int(output_count))
        self.input_count = len(self.matrix.inputs)
        self.output_count = len(self.matrix.outputs)
        self.config_store.set_counts(self.input_count, self.output_count)
        return {"input_count": self.input_count, "output_count": self.output_count}

    def _resize_inputs(self, target: int) -> None:
        if target < 1:
            raise ValueError("input_count must be at least 1")
        while len(self.matrix.inputs) < target:
            slot = self.matrix.add_input()
            self.matrix.configure_input(slot.id, state=SlotState.INACTIVE)
            index = len(self.matrix.inputs)
            self.input_scene_map.setdefault(slot.id, f"PGM - Player {index:02d}")
            self.obs.input_scene_map.setdefault(slot.id, f"PGM - Player {index:02d}")
        while len(self.matrix.inputs) > target:
            removed = self.matrix.remove_last_input()
            if self.media is not None:
                self.media.remove_input(removed)
            self.input_scene_map.pop(removed, None)
            self.obs.input_scene_map.pop(removed, None)
            self.config_store.drop_input(removed)
        self._sync_compositor_inputs()

    def _resize_outputs(self, target: int) -> None:
        if target < 1:
            raise ValueError("output_count must be at least 1")
        while len(self.matrix.outputs) < target:
            slot = self.matrix.add_output()
            self.matrix.configure_output(
                slot.id, kind=OutputKind.RESERVED, state=SlotState.RESERVED
            )
        while len(self.matrix.outputs) > target:
            last_id = self.matrix.last_output_id()
            if last_id == self.program_output:
                raise ValueError("cannot remove the program output slot")
            self.matrix.remove_last_output()

    def config_snapshot(self) -> dict:
        return {
            "sport_mode": self.sport_mode,
            "game_profile": self.config_store.game_profile,
            "director_mode": self.director_mode.value,
            "main_input": self.main_input,
            "cv_profile": self.cv_profile_name,
            "program_output": self.program_output,
            "input_count": len(self.matrix.inputs),
            "output_count": len(self.matrix.outputs),
            "program": {
                "output_id": self.program_output,
                "backend": self.program_output_backend,
                "publish_url": self.program.publish_url,
                "obs_url": self.program_obs_url(),
                "source_input": self.program.current_input,
                "running": self.program.is_running(),
                "active": self.program.active,
            },
            "virtualcam": {
                "enabled": self.virtualcam_enabled,
                **self.virtualcam.status(),
            },
            "host": {
                "primary_ip": primary_ip(),
                "addresses": local_ip_addresses(),
                "public_ip": public_ip(),
            },
            "inputs": [
                {
                    "id": slot.id,
                    "label": slot.label,
                    "player_name": slot.player_name,
                    "team": slot.team,
                    "role": slot.role,
                    "obs_scene": self.obs.input_scene_map.get(slot.id),
                    "state": slot.state.value,
                    "uri": slot.uri,
                    "source_mode": self._resolve_source_mode(
                        self.config_store.input_config(slot.id)
                    ),
                    "srt_mode": self.config_store.input_config(slot.id).get("srt_mode", ""),
                    "srt_host": self.config_store.input_config(slot.id).get("srt_host", ""),
                    "srt_port": self.config_store.input_config(slot.id).get("srt_port", ""),
                    "srt_latency": self.config_store.input_config(slot.id).get(
                        "srt_latency", ""
                    ),
                    "file_path": self.config_store.input_config(slot.id).get(
                        "file_path", ""
                    ),
                    "source_url": self.config_store.input_config(slot.id).get(
                        "source_url", ""
                    ),
                    "loop": _as_bool(self.config_store.input_config(slot.id).get("loop")),
                }
                for slot in self.matrix.inputs.values()
            ],
            "outputs": [
                {
                    "id": slot.id,
                    "label": slot.label,
                    "kind": slot.kind.value,
                    "uri": slot.uri,
                }
                for slot in self.matrix.outputs.values()
            ],
        }

    def enable_media(
        self,
        inputs: list[MediaInput],
        preview_dir: str = ".preview",
        freshness_ms: float = 2000.0,
    ) -> None:
        """Switch from simulated frame taps to real FFmpeg media ingest."""
        self._media_preview_dir = preview_dir
        self._media_freshness_ms = freshness_ms
        self._ensure_media()
        assert self.media is not None
        configured_input_ids = {spec.input_id for spec in inputs}
        for slot in self.matrix.inputs.values():
            if slot.id not in configured_input_ids:
                self.matrix.configure_input(
                    slot.id,
                    state=SlotState.INACTIVE,
                    protocol=None,
                    uri=None,
                    last_frame_at=None,
                )
        for spec in inputs:
            if spec.input_id not in self.matrix.inputs:
                logger.warning(
                    "media config references unknown slot %s; skipping", spec.input_id
                )
                continue
            self.matrix.configure_input(
                spec.input_id,
                state=SlotState.MISSING_SIGNAL,
                protocol=spec.source_type,
                uri=spec.read_url,
            )
            self.media.set_input(spec)

    def preview_jpeg(self, input_id: str) -> bytes | None:
        if self.media is None:
            return None
        return self.media.latest_jpeg(input_id)

    @property
    def agent_enabled(self) -> bool:
        # A manually-suspended director is not "enabled": it will not cut until
        # the operator explicitly resumes. Clients read director_mode for the
        # three-way distinction.
        if self.sport_mode == "soccer" and self.soccer_director is not None:
            return self.director_mode == DirectorMode.AUTOMATIC
        return self.director.enabled

    def set_agent_enabled(self, enabled: bool) -> bool:
        """Activate or deactivate the AI director.

        When deactivated the agent stops proposing cuts (both story-driven and
        idle rotation); only operator commands move the program. The choice is
        persisted so it survives a restart. For soccer, ``enabled=True`` is the
        explicit resume from manual suspension.
        """
        value = _as_bool(enabled)
        if self.sport_mode == "soccer":
            mode = DirectorMode.AUTOMATIC if value else DirectorMode.DISABLED
            self.set_director_mode(mode)
            logger.info("AI director %s", "activated" if value else "deactivated")
            return value
        self.director.enabled = value
        self.config_store.set_agent_enabled(value)
        self.director_mode = (
            DirectorMode.AUTOMATIC if value else DirectorMode.DISABLED
        )
        logger.info("AI director %s", "activated" if value else "deactivated")
        return value

    def set_director_mode(self, mode: DirectorMode | str, persist: bool = True) -> str:
        value = DirectorMode(mode)
        self.director_mode = value
        if self.soccer_director is not None:
            self.soccer_director.set_mode(value)
        self.director.enabled = (
            self.sport_mode != "soccer" and value == DirectorMode.AUTOMATIC
        )
        if value != DirectorMode.MANUAL_SUSPENDED:
            self.suspension = None
        if persist:
            self.config_store.set_director_mode(value.value)
            if value != DirectorMode.MANUAL_SUSPENDED:
                self.config_store.set_suspension(None)
        return value.value

    def suspend_automation(self, operator: str, reason: str) -> None:
        if self.director_mode == DirectorMode.DISABLED:
            return
        persist = (
            self.soccer_profile is None
            or self.soccer_profile.manual_suspension.persist
        )
        self.suspension = {"by": operator, "reason": reason, "at": now_ms()}
        self.set_director_mode(DirectorMode.MANUAL_SUSPENDED, persist=persist)
        if persist:
            self.config_store.set_suspension(self.suspension)
        logger.info("director suspended by %s reason=%s", operator, reason)

    def start_director(self) -> None:
        """Spin up the idle-rotation or soccer decision ticker (idempotent)."""
        if self._director_thread is not None:
            return
        self._director_stop.clear()

        interval = 0.2 if self.sport_mode == "soccer" else max(
            0.15, self.rotate_interval_ms / 1000.0
        )

        def _loop() -> None:
            while not self._director_stop.wait(interval):
                try:
                    if self.sport_mode == "soccer":
                        self.tick_soccer()
                    else:
                        self.director.rotate_idle()
                except Exception:  # pragma: no cover - defensive, keep ticker alive
                    logger.exception("director ticker failed")

        self._director_thread = threading.Thread(
            target=_loop, name="director-rotate", daemon=True
        )
        self._director_thread.start()
        logger.info(
            "Director ticker started sport=%s interval=%.0f ms",
            self.sport_mode,
            interval * 1000.0,
        )

    def stop_director(self) -> None:
        self._director_stop.set()
        thread = self._director_thread
        self._director_thread = None
        if (
            thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(timeout=2.0)

    def set_sport_mode(self, sport: str, game_profile: str | None = None) -> dict:
        """Switch the active sport at runtime; persisted across restarts.

        Rebuilds the sport-specific director stack in place. If real media
        ingest is already running, its decode settings (pixel loading for CV)
        only apply after a service restart, which the response flags.
        """
        if game_profile is not None:
            self.config_store.set_game_profile(game_profile or None)
        value = self.config_store.set_sport_mode(sport)
        changed = value != self.sport_mode
        self.sport_mode = value
        if changed:
            if value == "soccer":
                self._init_soccer()
                self.soccer_director.set_mode(self.director_mode)
            else:
                self.soccer_profile = None
                self.soccer = None
                self.soccer_fusion = None
                self.soccer_cameras = None
                self.soccer_director = None
                self.vision_agent = None
                self._cv_runtime = None
                self.main_input = self.config_store.main_input
            self.director.enabled = (
                value != "soccer" and self.director_mode == DirectorMode.AUTOMATIC
            )
            if self._director_thread is not None:
                self.stop_director()
                self.start_director()
            logger.info("sport mode switched to %s", value)
        return {
            "sport_mode": value,
            "game_profile": self.config_store.game_profile,
            "changed": changed,
            "media_restart_required": bool(
                changed and value == "soccer" and self.media is not None
            ),
        }

    def connect_obs(self) -> None:
        self.obs.connect()

    def feed_gsi(self, payload: dict) -> None:
        """Push one GSI payload through the whole pipeline."""
        if self.sport_mode == "soccer":
            return
        state = self.gsi.ingest(payload)
        if self.gsi.is_fresh():
            self.intelligence.update(state)
        self.latency.record(
            "gsi_to_game_state", state.age_ms(), self.budget.gsi_to_game_state_ms
        )

    def feed_soccer_events(self, payload: dict) -> dict:
        """Ingest one structured soccer heartbeat or event revision."""
        if self.soccer is None or self.soccer_fusion is None:
            raise RuntimeError("soccer director is not active")
        item = self.soccer.ingest(payload)
        view = self.soccer_fusion.ingest_structured(item)
        if self.soccer_director is not None:
            self.soccer_director.tick()
        snapshot = view.snapshot()
        snapshot["accepted"] = item is not None
        snapshot["rejected_total"] = self.soccer.rejected
        return snapshot

    def tick_soccer(self) -> None:
        frames = self.frames.tap_all()
        if self.vision_agent is not None:
            self.vision_agent.process(frames)
        if self.soccer_cameras is not None and self.soccer_fusion is not None:
            for assignment in self.soccer_fusion.cameras.values():
                self.soccer_cameras.update(assignment)
        if self.soccer_director is not None:
            self.soccer_director.tick()

    def tick_frames(self) -> None:
        self.frames.tap_all()

    def state_snapshot(self) -> dict:
        return {
            "media_enabled": self.media is not None,
            "agent_enabled": self.agent_enabled,
            "sport_mode": self.sport_mode,
            "director_mode": self.director_mode.value,
            "main_input": self.main_input,
            "soccer": self._soccer_snapshot(),
            "program": self.program.status(),
            "virtualcam": {
                "enabled": self.virtualcam_enabled,
                **self.virtualcam.status(),
            },
            "matrix": self.matrix.snapshot(),
            "buses": self.buses.stats_snapshot(),
            "latency": self.latency.snapshot(),
            "audit": self.routing.audit_snapshot(),
            "intelligence": [
                {
                    "type": event.type,
                    "priority": event.priority,
                    "confidence": event.confidence,
                    "ttl_ms": event.ttl_ms,
                    "age_ms": event.age_ms(),
                    "stale": event.is_stale(),
                    "affected_players": event.affected_players,
                    "recommended_inputs": event.recommended_inputs,
                    "evidence": event.evidence,
                    "lifecycle": event.lifecycle,
                    "source": event.source,
                    "correlation_id": event.correlation_id,
                    "phase": event.phase,
                }
                for event in self.buses.intelligence.recent(20)
            ],
            "match": self._match_snapshot(),
        }

    def _soccer_snapshot(self) -> dict | None:
        if self.soccer_fusion is None:
            return None
        view = self.soccer_fusion.view()
        payload = view.snapshot()
        payload["director_mode"] = self.director_mode.value
        payload["main_input"] = self.main_input
        payload["cv_profile"] = self.cv_profile_name
        payload["vision_degraded"] = (
            self.vision_agent.degraded if self.vision_agent is not None else False
        )
        payload["vision_routing_allowed"] = (
            self.vision_agent.routing_allowed if self.vision_agent is not None else False
        )
        payload["rejected"] = self.soccer.rejected if self.soccer is not None else 0
        payload["suspension"] = self.suspension
        payload["warnings"] = [
            item
            for item in (
                view.fallback_reason,
                "heartbeat_overdue"
                if self.soccer is not None and self.soccer.heartbeat_overdue()
                else None,
                "cv_shadow" if self.vision_agent and not self.vision_agent.routing_allowed else None,
                "cv_degraded" if self.vision_agent and self.vision_agent.degraded else None,
            )
            if item
        ]
        if self.soccer_cameras is not None:
            payload["cameras"] = self.soccer_cameras.snapshot()
        return payload

    def _match_snapshot(self) -> dict | None:
        state = self.gsi.state
        if state is None:
            return None
        return {
            "round_id": state.round_id,
            "phase": state.phase,
            "round_number": state.round_number,
            "map_name": state.map_name,
            "fresh": self.gsi.is_fresh(),
            "age_ms": state.age_ms(),
            "bomb": {
                "state": state.bomb.state,
                "site": state.bomb.site,
                "countdown_ms": state.bomb.countdown_ms,
                "player_slot": state.bomb.player_slot,
            },
            "players": [
                {
                    "slot": player.slot,
                    "input_id": player.input_id,
                    "name": player.name,
                    "team": player.team,
                    "health": player.health,
                    "alive": player.alive,
                    "active_weapon": player.active_weapon,
                    "round_kills": player.round_kills,
                    "match_kills": player.match_kills,
                }
                for player in state.players.values()
            ],
        }


def build_default_service(
    obs_dry_run: bool = True,
    sport_mode: str | None = None,
    game_profile: str | None = None,
) -> MatrixService:
    if game_profile is not None:
        MatrixConfigStore("configs/matrix.runtime.json").set_game_profile(game_profile)
    return MatrixService(obs_dry_run=obs_dry_run, sport_mode=sport_mode)
