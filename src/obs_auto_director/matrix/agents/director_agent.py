from __future__ import annotations

import logging

from obs_auto_director.matrix.buses import MatrixBuses
from obs_auto_director.matrix.domain import (
    BusName,
    GameStateEvent,
    MatchIntelligenceEvent,
    RoutingIntent,
)
from obs_auto_director.matrix.matrix_core import MatrixCore

logger = logging.getLogger(__name__)

# Primitive game-state events the director can fall back on, with base priority.
GAME_STATE_PRIORITY = {
    "player_low_health": 45,
    "sniper_equipped": 55,
    "grenade_equipped": 50,
    "player_burning": 48,
    "bomb_planted": 65,
    "player_death": 30,
}

# Idle-rotation fallback: when no story is driving a cut, the director keeps the
# program moving across live inputs at this (deliberately low) priority so any
# fresh story preempts it instantly. Dwell time per feed is paced by the caller
# (the service ticker), so the intent itself sets no hold.
ROTATE_PRIORITY = 10
ROTATE_CONFIDENCE = 0.3
ROTATE_TTL_MS = 2000.0
DEFAULT_ROTATE_INTERVAL_MS = 6000.0


class DirectorAgent:
    """Proposes live cuts. Producer of intents, never an owner of routes.

    Preference order, matching the plan:
      1. Story-level intelligence (fresh) -> strongest intents.
      2. Fresh primitive game-state events -> fallback intents.
      3. Vision/frame taps -> confirmation only (handled by the frame manager).
    """

    def __init__(
        self,
        buses: MatrixBuses,
        matrix: MatrixCore,
        program_output: str = "output_01",
        steam_to_input: dict[str, str] | None = None,
        name: str = "director-agent",
        enabled: bool = True,
    ) -> None:
        self.buses = buses
        self.matrix = matrix
        self.program_output = program_output
        self.steam_to_input = steam_to_input or {}
        self.name = name
        # When disabled, the agent stops proposing cuts entirely (both story
        # cuts and idle rotation), leaving the operator in full manual control.
        self.enabled = enabled
        buses.intelligence.subscribe(self.on_intelligence)
        buses.game_state.subscribe(self.on_game_state)

    def on_intelligence(self, event: MatchIntelligenceEvent) -> RoutingIntent | None:
        if not self.enabled:
            return None
        if event.is_stale():
            logger.debug("director ignoring stale story %s", event.type)
            return None
        candidate = self._first_available(event.recommended_inputs)
        if candidate is None:
            return None
        intent = RoutingIntent(
            producer=self.name,
            bus=BusName.DECISION,
            target_output=self.program_output,
            candidate_input=candidate,
            reason=f"story:{event.type}",
            priority=event.priority,
            confidence=event.confidence,
            ttl_ms=event.ttl_ms,
            interrupt=event.priority >= 90,
            hold_ms=1500.0,
        )
        self.buses.decision.publish(intent)
        return intent

    def on_game_state(self, event: GameStateEvent) -> RoutingIntent | None:
        if not self.enabled:
            return None
        priority = GAME_STATE_PRIORITY.get(event.type)
        if priority is None:
            return None
        candidate = self._resolve_input(event)
        if candidate is None or not self.matrix.input_available(candidate):
            return None
        intent = RoutingIntent(
            producer=self.name,
            bus=BusName.DECISION,
            target_output=self.program_output,
            candidate_input=candidate,
            reason=f"game_state:{event.type}",
            priority=priority,
            confidence=0.7,
            ttl_ms=3000.0,
            interrupt=False,
            hold_ms=800.0,
        )
        self.buses.decision.publish(intent)
        return intent

    def rotate_idle(self) -> RoutingIntent | None:
        """Advance the program to the next live input (round-robin).

        This is the "rotate when nothing notable is happening" fallback. It is
        published at ROTATE_PRIORITY so any fresh story (priority 45+) preempts
        it; the caller paces how often this is called so each feed gets screen
        time. Returns the published intent, or None when disabled / no live
        inputs.
        """
        if not self.enabled:
            return None
        available = [
            slot_id for slot_id, slot in self.matrix.inputs.items() if slot.has_signal
        ]
        if not available:
            return None
        current = self.matrix.current_input_for(self.program_output)
        candidate = self._next_in_cycle(available, current)
        intent = RoutingIntent(
            producer=self.name,
            bus=BusName.DECISION,
            target_output=self.program_output,
            candidate_input=candidate,
            reason="idle_rotation",
            priority=ROTATE_PRIORITY,
            confidence=ROTATE_CONFIDENCE,
            ttl_ms=ROTATE_TTL_MS,
            interrupt=False,
            hold_ms=0.0,
        )
        self.buses.decision.publish(intent)
        return intent

    @staticmethod
    def _next_in_cycle(available: list[str], current: str | None) -> str:
        if current in available:
            return available[(available.index(current) + 1) % len(available)]
        return available[0]

    def _resolve_input(self, event: GameStateEvent) -> str | None:
        if event.player_slot is None:
            return None
        return self.steam_to_input.get(event.player_slot, event.player_slot)

    def _first_available(self, candidates: list[str]) -> str | None:
        for candidate in candidates:
            resolved = self.steam_to_input.get(candidate, candidate)
            if self.matrix.input_available(resolved):
                return resolved
        return None
