from __future__ import annotations

import logging
from typing import Callable

from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import now_ms
from excalibur.matrix.domain import (
    BusName,
    GameStateEvent,
    MatchIntelligenceEvent,
    MatchState,
    PlayerState,
    RoutingIntent,
)
from excalibur.matrix.gsi.receiver import SNIPER_WEAPONS
from excalibur.matrix.matrix_core import MatrixCore

logger = logging.getLogger(__name__)

# Primitive game-state events the director can fall back on, with base priority.
# player_death is deliberately absent: it names the victim, and cutting to a
# corpse's POV is the wrong shot -- player_kill (the killer) covers the moment.
GAME_STATE_PRIORITY = {
    "player_low_health": 45,
    "sniper_equipped": 55,
    "grenade_equipped": 50,
    "player_burning": 48,
    "bomb_planted": 65,
}

# A single kill is a solid cut; each further kill in the round raises the
# stakes (and the priority) until a 3k+ can interrupt whatever is on program.
KILL_BASE_PRIORITY = 65
KILL_STEP_PRIORITY = 11
KILL_MAX_PRIORITY = 97
KILL_INTERRUPT_AT = 3

# Idle-rotation fallback: when no story is driving a cut, the director keeps the
# program moving across live inputs at this (deliberately low) priority so any
# fresh story preempts it instantly. Dwell time per feed is paced by the caller
# (the service ticker), so the intent itself sets no hold.
ROTATE_PRIORITY = 10
ROTATE_CONFIDENCE = 0.3
ROTATE_TTL_MS = 2000.0
DEFAULT_ROTATE_INTERVAL_MS = 6000.0

# After a story-driven cut, idle rotation stays quiet for the story's hold plus
# this grace so a lull between story refreshes doesn't yank the camera away.
STORY_ROTATE_GRACE_MS = 2500.0


class DirectorAgent:
    """Proposes live cuts. Producer of intents, never an owner of routes.

    Preference order, matching the plan:
      1. Story-level intelligence (fresh) -> strongest intents.
      2. Fresh primitive game-state events -> fallback intents.
      3. Vision/frame taps -> confirmation only (handled by the frame manager).

    When a `state_provider` is supplied (the GSI receiver's latest MatchState),
    the agent uses match facts to pick the most dramatic candidate among a
    story's recommended inputs and to keep idle rotation off dead players'
    feeds.
    """

    def __init__(
        self,
        buses: MatrixBuses,
        matrix: MatrixCore,
        program_output: str = "output_01",
        steam_to_input: dict[str, str] | None = None,
        name: str = "director-agent",
        enabled: bool = True,
        state_provider: Callable[[], MatchState | None] | None = None,
    ) -> None:
        self.buses = buses
        self.matrix = matrix
        self.program_output = program_output
        self.steam_to_input = steam_to_input or {}
        self.name = name
        # When disabled, the agent stops proposing cuts entirely (both story
        # cuts and idle rotation), leaving the operator in full manual control.
        self.enabled = enabled
        self.state_provider = state_provider
        # Timestamp (ms) until which idle rotation defers to an active story.
        self._story_hold_until: float = 0.0
        buses.intelligence.subscribe(self.on_intelligence)
        buses.game_state.subscribe(self.on_game_state)

    def on_intelligence(self, event: MatchIntelligenceEvent) -> RoutingIntent | None:
        if not self.enabled:
            return None
        if event.is_stale():
            logger.debug("director ignoring stale story %s", event.type)
            return None
        candidate = self._best_available(event.recommended_inputs)
        if candidate is None:
            return None
        hold_ms = self._story_hold_ms(event.priority)
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
            hold_ms=hold_ms,
        )
        self._story_hold_until = now_ms() + hold_ms + STORY_ROTATE_GRACE_MS
        self.buses.decision.publish(intent)
        return intent

    def on_game_state(self, event: GameStateEvent) -> RoutingIntent | None:
        if not self.enabled:
            return None
        if event.type == "player_kill":
            return self._on_player_kill(event)
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

    def _on_player_kill(self, event: GameStateEvent) -> RoutingIntent | None:
        """Cut to the killer, escalating with their kill count this round."""
        candidate = self._resolve_input(event)
        if candidate is None or not self.matrix.input_available(candidate):
            return None
        kills = max(1, int(event.data.get("round_kills", 1)))
        priority = min(
            KILL_MAX_PRIORITY, KILL_BASE_PRIORITY + KILL_STEP_PRIORITY * (kills - 1)
        )
        hold_ms = 1500.0 + 500.0 * min(kills, 5)
        intent = RoutingIntent(
            producer=self.name,
            bus=BusName.DECISION,
            target_output=self.program_output,
            candidate_input=candidate,
            reason=f"game_state:player_kill:{kills}k",
            priority=priority,
            confidence=0.9,
            ttl_ms=3500.0,
            interrupt=kills >= KILL_INTERRUPT_AT,
            hold_ms=hold_ms,
        )
        self._story_hold_until = now_ms() + hold_ms + STORY_ROTATE_GRACE_MS
        self.buses.decision.publish(intent)
        return intent

    def rotate_idle(self) -> RoutingIntent | None:
        """Advance the program to the next live input (round-robin).

        This is the "rotate when nothing notable is happening" fallback. It is
        published at ROTATE_PRIORITY so any fresh story (priority 45+) preempts
        it; the caller paces how often this is called so each feed gets screen
        time. Returns the published intent, or None when disabled / no live
        inputs / a story recently drove a cut and still deserves the screen.
        """
        if not self.enabled:
            return None
        if now_ms() < self._story_hold_until:
            logger.debug("idle rotation deferring to active story hold")
            return None
        available = [
            slot_id for slot_id, slot in self.matrix.inputs.items() if slot.has_signal
        ]
        available = self._prefer_alive(available)
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

    @staticmethod
    def _story_hold_ms(priority: int) -> float:
        """Higher-stakes stories deserve a longer guaranteed hold on program."""
        return max(1500.0, min(4000.0, priority * 40.0))

    def _match_state(self) -> MatchState | None:
        if self.state_provider is None:
            return None
        return self.state_provider()

    def _players_by_input(self, state: MatchState) -> dict[str, PlayerState]:
        return {
            (player.input_id or player.slot): player
            for player in state.players.values()
        }

    def _prefer_alive(self, available: list[str]) -> list[str]:
        """Drop feeds of dead players from the rotation when facts are known.

        Feeds with no player mapping (observer, casters) are kept. If filtering
        would leave nothing, the unfiltered list is used so the program never
        freezes on an empty pool.
        """
        state = self._match_state()
        if state is None or not state.players:
            return available
        by_input = self._players_by_input(state)
        alive = [
            slot_id
            for slot_id in available
            if slot_id not in by_input or by_input[slot_id].alive
        ]
        return alive or available

    def _resolve_input(self, event: GameStateEvent) -> str | None:
        if event.player_slot is None:
            return None
        return self.steam_to_input.get(event.player_slot, event.player_slot)

    def _best_available(self, candidates: list[str]) -> str | None:
        """Pick the most watchable available input among a story's candidates.

        Without match state this preserves the old first-available behaviour.
        With it, candidates are ranked by drama: alive beats dead, low health
        and kills raise the stakes, an active sniper is a bonus.
        """
        resolved = [
            self.steam_to_input.get(candidate, candidate) for candidate in candidates
        ]
        available = [item for item in resolved if self.matrix.input_available(item)]
        if not available:
            return None
        state = self._match_state()
        if state is None or not state.players:
            return available[0]
        by_input = self._players_by_input(state)
        return max(available, key=lambda item: self._drama_score(by_input.get(item)))

    @staticmethod
    def _drama_score(player: PlayerState | None) -> float:
        if player is None:
            return 0.0
        if not player.alive:
            return -100.0
        score = 100.0
        score += (100 - player.health) * 0.2  # a hurt hero is a tense shot
        score += player.round_kills * 15.0
        if player.active_weapon in SNIPER_WEAPONS:
            score += 5.0
        return score
