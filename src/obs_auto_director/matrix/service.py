from __future__ import annotations

import logging

from obs_auto_director.config import ObsConfig
from obs_auto_director.matrix.agents.director_agent import DirectorAgent
from obs_auto_director.matrix.buses import MatrixBuses
from obs_auto_director.matrix.domain import (
    OutputKind,
    SlotState,
    input_slot_id,
    output_slot_id,
)
from obs_auto_director.matrix.frame_tap import FrameTapManager, SimulatedFrameTapSource
from obs_auto_director.matrix.gsi.receiver import GsiReceiver
from obs_auto_director.matrix.gsi.simulator import ROSTER
from obs_auto_director.matrix.intelligence.engine import MatchIntelligenceEngine
from obs_auto_director.matrix.latency import LatencyBudget, LatencyTracker
from obs_auto_director.matrix.matrix_core import MatrixCore
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
    ) -> None:
        self.budget = budget or LatencyBudget()
        self.latency = LatencyTracker(self.budget)
        self.program_output = program_output

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

        self.input_scene_map = {
            input_slot_id(index): f"PGM - Player {index:02d}" for index in range(1, 12)
        }
        self.obs = ObsBridge(
            ObsSceneRouter(ObsConfig(dry_run=obs_dry_run)),
            program_output=program_output,
            input_scene_map=self.input_scene_map,
            output_scene_map={program_output: "PGM - Program"},
        )
        self.routing = RoutingEngine(
            self.matrix,
            self.buses,
            budget=self.budget,
            on_route_applied=self.obs.on_route_applied,
        )

    def _configure_slots(self, active_inputs: int, active_outputs: int) -> None:
        for index in range(1, 12):
            slot_id = input_slot_id(index)
            steamid, name, team = ROSTER[index - 1] if index <= len(ROSTER) else (None, None, None)
            if index <= active_inputs:
                self.matrix.configure_input(
                    slot_id,
                    state=SlotState.ACTIVE,
                    protocol="srt",
                    uri=f"srt://0.0.0.0:{10000 + index}",
                    player_name=name,
                    team=team,
                    role="player",
                )
            else:
                self.matrix.configure_input(
                    slot_id,
                    state=SlotState.RESERVED if index <= len(ROSTER) else SlotState.INACTIVE,
                    player_name=name,
                    team=team,
                )

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
