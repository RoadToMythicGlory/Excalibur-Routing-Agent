from __future__ import annotations

import logging

from obs_auto_director.config import ObsConfig
from obs_auto_director.matrix.agents.director_agent import DirectorAgent
from obs_auto_director.matrix.buses import MatrixBuses
from obs_auto_director.matrix.config_store import MatrixConfigStore
from obs_auto_director.matrix.domain import (
    OutputKind,
    SlotState,
    input_slot_id,
    output_slot_id,
)
from obs_auto_director.matrix.frame_tap import FrameTapManager, SimulatedFrameTapSource
from obs_auto_director.matrix.media.ingest import (
    MediaIngestManager,
    MediaInput,
    build_srt_url,
)
from obs_auto_director.matrix.media.output import (
    DEFAULT_PROGRAM_PUBLISH_URL,
    ProgramOutputManager,
    program_obs_url,
)
from obs_auto_director.matrix.gsi.receiver import GsiReceiver
from obs_auto_director.matrix.gsi.simulator import ROSTER
from obs_auto_director.matrix.intelligence.engine import MatchIntelligenceEngine
from obs_auto_director.matrix.latency import LatencyBudget, LatencyTracker
from obs_auto_director.matrix.matrix_core import MatrixCore
from obs_auto_director.matrix.net import local_ip_addresses, primary_ip, public_ip
from obs_auto_director.matrix.obs_bridge import ObsBridge
from obs_auto_director.matrix.routing_engine import RoutingEngine
from obs_auto_director.obs.client import ObsSceneRouter

logger = logging.getLogger(__name__)


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
    ) -> None:
        self.budget = budget or LatencyBudget()
        self.latency = LatencyTracker(self.budget)
        self.config_store = MatrixConfigStore(config_path)
        self.program_output = self.config_store.program_output or program_output

        self.matrix = MatrixCore()
        self.buses = MatrixBuses()

        self.steam_to_input = {
            steamid: input_slot_id(index + 1) for index, (steamid, _, _) in enumerate(ROSTER)
        }
        self._configure_slots(active_inputs, active_outputs)

        self.gsi = GsiReceiver(
            self.buses, budget=self.budget, steam_to_input=self.steam_to_input
        )
        self.intelligence = MatchIntelligenceEngine(self.buses)
        self.director = DirectorAgent(
            self.buses,
            self.matrix,
            program_output=program_output,
            steam_to_input=self.steam_to_input,
        )

        self.frame_source = SimulatedFrameTapSource()
        self.frames = FrameTapManager(self.matrix, self.frame_source, budget=self.budget)
        self.media: MediaIngestManager | None = None
        self._media_preview_dir = ".preview"
        self._media_freshness_ms = 2000.0

        self.input_scene_map = {
            input_slot_id(index): f"PGM - Player {index:02d}" for index in range(1, 12)
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
        self.program = ProgramOutputManager(self.program_publish_url)
        self.matrix.configure_output(self.program_output, uri=self.program_publish_url)

        self.routing = RoutingEngine(
            self.matrix,
            self.buses,
            budget=self.budget,
            on_route_applied=self._on_route_applied,
        )

        self._apply_config_store()

    def _configure_slots(self, active_inputs: int, active_outputs: int) -> None:
        for index in range(1, 12):
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
        for index in range(1, 12):
            slot_id = output_slot_id(index)
            kind = output_kinds.get(index, OutputKind.RESERVED)
            state = SlotState.ACTIVE if index <= active_outputs else SlotState.RESERVED
            self.matrix.configure_output(slot_id, kind=kind, state=state)

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
        self._apply_srt_fields(input_id, fields)

    def _apply_srt_fields(self, input_id: str, fields: dict) -> None:
        mode = (fields.get("srt_mode") or "").strip().lower()
        port = fields.get("srt_port")
        if mode in ("off", "", "none"):
            if mode in ("off", "none") and self.media is not None:
                self.media.remove_input(input_id)
            return
        if mode not in ("listener", "caller") or not port:
            return
        url = build_srt_url(
            mode,
            fields.get("srt_host"),
            int(port),
            int(fields.get("srt_latency") or 200),
        )
        self._ensure_media()
        self.matrix.configure_input(
            input_id, state=SlotState.MISSING_SIGNAL, protocol="srt", uri=url
        )
        assert self.media is not None
        self.media.set_input(MediaInput(input_id=input_id, read_url=url))

    def _on_route_applied(self, route, decision) -> None:
        """Fan out an applied route to OBS layout switching and the program feed."""
        self.obs.on_route_applied(route, decision)
        self._update_program_output(route)

    def _update_program_output(self, route) -> None:
        """Point the Excalibur program output at the input now routed to program."""
        if route.output_id != self.program_output:
            return
        read_url = None
        if route.input_id is not None:
            slot = self.matrix.inputs.get(route.input_id)
            read_url = slot.uri if slot is not None else None
        self.program.set_source(route.input_id, read_url)

    def program_obs_url(self) -> str:
        """The URL an OBS media source should read to show the program feed."""
        return program_obs_url(self.program.publish_url, primary_ip())

    def _ensure_media(self) -> None:
        if self.media is not None:
            return
        self.media = MediaIngestManager(
            self.matrix,
            [],
            preview_dir=self._media_preview_dir,
            freshness_ms=self._media_freshness_ms,
        )
        self.frame_source = self.media.source
        self.frames = FrameTapManager(self.matrix, self.frame_source, budget=self.budget)
        self.media.start()
        # Real feeds exist now, so the program publisher may spawn FFmpeg.
        self.program.enable()

    def update_input_config(self, input_id: str, fields: dict) -> dict:
        """Operator sets metadata for an input; persisted and applied live."""
        if input_id not in self.matrix.inputs:
            raise KeyError(f"Unknown input slot: {input_id}")
        saved = self.config_store.set_input(input_id, fields)
        self._apply_input_fields(input_id, saved)
        return saved

    def config_snapshot(self) -> dict:
        return {
            "program_output": self.program_output,
            "program": {
                "output_id": self.program_output,
                "publish_url": self.program.publish_url,
                "obs_url": self.program_obs_url(),
                "source_input": self.program.current_input,
                "running": self.program.is_running(),
                "active": self.program.active,
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
                    "srt_mode": self.config_store.input_config(slot.id).get("srt_mode", ""),
                    "srt_host": self.config_store.input_config(slot.id).get("srt_host", ""),
                    "srt_port": self.config_store.input_config(slot.id).get("srt_port", ""),
                    "srt_latency": self.config_store.input_config(slot.id).get(
                        "srt_latency", ""
                    ),
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
            self.matrix.configure_input(
                spec.input_id,
                state=SlotState.MISSING_SIGNAL,
                protocol="srt",
                uri=spec.read_url,
            )
            self.media.set_input(spec)

    def preview_jpeg(self, input_id: str) -> bytes | None:
        if self.media is None:
            return None
        return self.media.latest_jpeg(input_id)

    def connect_obs(self) -> None:
        self.obs.connect()

    def feed_gsi(self, payload: dict) -> None:
        """Push one GSI payload through the whole pipeline."""
        state = self.gsi.ingest(payload)
        if self.gsi.is_fresh():
            self.intelligence.update(state)
        self.latency.record(
            "gsi_to_game_state", state.age_ms(), self.budget.gsi_to_game_state_ms
        )

    def tick_frames(self) -> None:
        self.frames.tap_all()

    def state_snapshot(self) -> dict:
        return {
            "media_enabled": self.media is not None,
            "program": self.program.status(),
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
                }
                for event in self.buses.intelligence.recent(20)
            ],
            "match": self._match_snapshot(),
        }

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
                }
                for player in state.players.values()
            ],
        }


def build_default_service(obs_dry_run: bool = True) -> MatrixService:
    return MatrixService(obs_dry_run=obs_dry_run)
