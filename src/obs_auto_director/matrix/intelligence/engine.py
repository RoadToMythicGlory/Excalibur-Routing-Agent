from __future__ import annotations

import logging
import statistics

from obs_auto_director.matrix.buses import MatrixBuses
from obs_auto_director.matrix.domain import (
    MatchIntelligenceEvent,
    MatchState,
    PlayerState,
)
from obs_auto_director.matrix.gsi.receiver import SNIPER_WEAPONS, GRENADE_WEAPONS

logger = logging.getLogger(__name__)

ECO_MONEY = 2000


class MatchIntelligenceEngine:
    """Turns raw game facts into broadcast stories.

    A director does not cut because `grenade_thrown`; it cuts because the
    grenade contributes to a `site_execute`, a `retake_started`, or a
    `potential_clutch`. This engine maintains rolling round context and emits
    story-level events to the Intelligence Bus, edge-triggered per round so it
    does not spam, and re-armed when the context changes.
    """

    def __init__(self, buses: MatrixBuses) -> None:
        self.buses = buses
        self._round_id: str | None = None
        self._armed: dict[str, bool] = {}

    def update(self, state: MatchState) -> list[MatchIntelligenceEvent]:
        if state.round_id != self._round_id:
            self._round_id = state.round_id
            self._armed = {}

        events: list[MatchIntelligenceEvent] = []
        for detector in (
            self._potential_clutch,
            self._last_alive,
            self._site_execute,
            self._retake_started,
            self._awp_duel,
            self._eco_upset_risk,
        ):
            event = detector(state)
            if event is not None:
                events.append(event)

        for event in events:
            self.buses.intelligence.publish(event)
            logger.info(
                "intelligence %s priority=%d confidence=%.2f inputs=%s",
                event.type,
                event.priority,
                event.confidence,
                event.recommended_inputs,
            )
        return events

    # -- detectors -----------------------------------------------------------
    def _potential_clutch(self, state: MatchState) -> MatchIntelligenceEvent | None:
        teams = state.alive_by_team()
        for team, players in teams.items():
            enemies = [count for other, count in teams.items() if other != team]
            enemy_alive = sum(len(c) for c in enemies)
            if len(players) == 1 and enemy_alive >= 2:
                hero = players[0]
                if not self._arm(f"clutch:{hero.slot}"):
                    return None
                bomb_planted = state.bomb.state in ("planted", "defusing")
                confidence = 0.7 + min(0.2, 0.05 * enemy_alive) + (0.05 if bomb_planted else 0)
                evidence = [
                    f"{hero.slot}_last_alive",
                    f"enemy_alive_count={enemy_alive}",
                    f"{hero.slot}_health={hero.health}",
                ]
                if bomb_planted:
                    evidence.append("bomb_planted=true")
                return MatchIntelligenceEvent(
                    type="potential_clutch",
                    round_id=state.round_id,
                    priority=92,
                    confidence=min(0.99, confidence),
                    ttl_ms=2500,
                    affected_players=[hero.slot],
                    recommended_inputs=self._inputs([hero]),
                    evidence=evidence,
                )
        return None

    def _last_alive(self, state: MatchState) -> MatchIntelligenceEvent | None:
        teams = state.alive_by_team()
        for team, players in teams.items():
            other_alive = sum(len(c) for o, c in teams.items() if o != team)
            if len(players) == 1 and other_alive >= 2:
                hero = players[0]
                if not self._arm(f"last_alive:{hero.slot}"):
                    return None
                return MatchIntelligenceEvent(
                    type="last_alive",
                    round_id=state.round_id,
                    priority=80,
                    confidence=0.9,
                    ttl_ms=3000,
                    affected_players=[hero.slot],
                    recommended_inputs=self._inputs([hero]),
                    evidence=[f"{hero.slot}_last_alive_for_{team}"],
                )
        return None

    def _site_execute(self, state: MatchState) -> MatchIntelligenceEvent | None:
        attackers = [
            player
            for player in state.alive_players()
            if player.team == "T" and player.active_weapon in GRENADE_WEAPONS
        ]
        if len(attackers) >= 2:
            if not self._arm("site_execute"):
                return None
            return MatchIntelligenceEvent(
                type="site_execute",
                round_id=state.round_id,
                priority=75,
                confidence=0.6 + min(0.3, 0.1 * len(attackers)),
                ttl_ms=2500,
                affected_players=[player.slot for player in attackers],
                recommended_inputs=self._inputs(attackers),
                evidence=[f"attackers_using_utility={len(attackers)}"],
            )
        return None

    def _retake_started(self, state: MatchState) -> MatchIntelligenceEvent | None:
        if state.bomb.state != "planted":
            self._armed.pop("retake_started", None)
            return None
        defenders = [p for p in state.alive_players() if p.team == "CT"]
        if defenders:
            if not self._arm("retake_started"):
                return None
            return MatchIntelligenceEvent(
                type="retake_started",
                round_id=state.round_id,
                priority=78,
                confidence=0.7,
                ttl_ms=3000,
                affected_players=[player.slot for player in defenders],
                recommended_inputs=self._inputs(defenders),
                evidence=[
                    "bomb_planted=true",
                    f"defenders_alive={len(defenders)}",
                    f"site={state.bomb.site}",
                ],
            )
        return None

    def _awp_duel(self, state: MatchState) -> MatchIntelligenceEvent | None:
        snipers = [
            player
            for player in state.alive_players()
            if player.active_weapon in SNIPER_WEAPONS
        ]
        teams = {player.team for player in snipers}
        if len(snipers) >= 2 and len(teams) >= 2:
            if not self._arm("awp_duel"):
                return None
            return MatchIntelligenceEvent(
                type="awp_duel",
                round_id=state.round_id,
                priority=82,
                confidence=0.75,
                ttl_ms=2000,
                affected_players=[player.slot for player in snipers],
                recommended_inputs=self._inputs(snipers),
                evidence=[f"snipers={[p.slot for p in snipers]}"],
            )
        return None

    def _eco_upset_risk(self, state: MatchState) -> MatchIntelligenceEvent | None:
        teams: dict[str, list[PlayerState]] = {}
        for player in state.alive_players():
            teams.setdefault(player.team, []).append(player)
        if len(teams) < 2:
            return None
        money = {
            team: statistics.mean([p.money for p in players]) if players else 0
            for team, players in teams.items()
        }
        eco_team = min(money, key=money.get)
        rich_team = max(money, key=money.get)
        if money[eco_team] <= ECO_MONEY and len(teams[eco_team]) > len(teams[rich_team]):
            if not self._arm("eco_upset_risk"):
                return None
            return MatchIntelligenceEvent(
                type="eco_upset_risk",
                round_id=state.round_id,
                priority=60,
                confidence=0.65,
                ttl_ms=4000,
                affected_players=[player.slot for player in teams[eco_team]],
                recommended_inputs=self._inputs(teams[eco_team]),
                evidence=[
                    f"eco_team={eco_team}",
                    f"eco_avg_money={money[eco_team]:.0f}",
                    f"eco_alive={len(teams[eco_team])}>rich_alive={len(teams[rich_team])}",
                ],
            )
        return None

    # -- helpers -------------------------------------------------------------
    def _arm(self, key: str) -> bool:
        """Edge-trigger: emit once per round per key until the condition resets."""
        if self._armed.get(key):
            return False
        self._armed[key] = True
        return True

    def _inputs(self, players: list[PlayerState]) -> list[str]:
        return [player.input_id or player.slot for player in players]
