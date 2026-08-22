from __future__ import annotations

import logging
import statistics

from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import now_ms
from excalibur.matrix.domain import (
    MatchIntelligenceEvent,
    MatchState,
    PlayerState,
)
from excalibur.matrix.gsi.receiver import SNIPER_WEAPONS, GRENADE_WEAPONS

logger = logging.getLogger(__name__)

ECO_MONEY = 2000
# Bomb countdown threshold below which the round enters its most dramatic beat.
BOMB_CRITICAL_MS = 10_000.0
# Priority ladder for multi-kills within a round: 2k, 3k, 4k, ace.
MULTI_KILL_PRIORITY = {2: 84, 3: 90, 4: 94, 5: 97}


class MatchIntelligenceEngine:
    """Turns raw game facts into broadcast stories.

    A director does not cut because `grenade_thrown`; it cuts because the
    grenade contributes to a `site_execute`, a `retake_started`, or a
    `potential_clutch`. This engine maintains rolling round context and emits
    story-level events to the Intelligence Bus.

    Stories are *sustained*, not one-shot: while a condition holds, the story
    re-emits every `refresh_ms` (lifecycle "update") so the director keeps
    holding the right camera through a long clutch or defuse instead of losing
    it when the first event's TTL expires. When the condition breaks, the story
    is released and may fire again later in the round (lifecycle "start").
    """

    def __init__(self, buses: MatrixBuses) -> None:
        self.buses = buses
        self._round_id: str | None = None
        # story key -> last emit timestamp (ms). Presence means "condition held
        # on the previous update"; absence means the next emit is a fresh start.
        self._active: dict[str, float] = {}
        self._prev: MatchState | None = None

    def update(self, state: MatchState) -> list[MatchIntelligenceEvent]:
        if state.round_id != self._round_id:
            self._round_id = state.round_id
            self._active = {}
            self._prev = None

        if state.phase == "over":
            # Round is decided; stop driving cuts and let the next round's
            # stories start clean.
            self._active = {}
            self._prev = state
            return []

        now = now_ms()
        events: list[MatchIntelligenceEvent] = []
        for detector in (
            self._kill_stories,
            self._defuse_drama,
            self._bomb_last_seconds,
            self._potential_clutch,
            self._last_alive,
            self._site_execute,
            self._retake_started,
            self._awp_duel,
            self._eco_upset_risk,
        ):
            produced = detector(state, now)
            if produced:
                events.extend(produced)
        self._prev = state

        for event in events:
            event.phase = state.phase
            self.buses.intelligence.publish(event)
            logger.info(
                "intelligence %s lifecycle=%s priority=%d confidence=%.2f inputs=%s",
                event.type,
                event.lifecycle,
                event.priority,
                event.confidence,
                event.recommended_inputs,
            )
        return events

    # -- detectors -----------------------------------------------------------
    def _kill_stories(self, state: MatchState, now: float) -> list[MatchIntelligenceEvent]:
        """Multi-kill and opening-kill stories from round_kills deltas."""
        if self._prev is None:
            return []
        events: list[MatchIntelligenceEvent] = []
        prev_players = self._prev.players
        prev_total = sum(p.round_kills for p in prev_players.values())
        for slot, player in state.players.items():
            prev = prev_players.get(slot)
            prev_kills = prev.round_kills if prev is not None else 0
            if player.round_kills <= prev_kills:
                continue

            if prev_total == 0 and player.round_kills >= 1:
                lifecycle = self._track("opening_kill", now, refresh_ms=None)
                if lifecycle is not None:
                    events.append(
                        MatchIntelligenceEvent(
                            type="opening_kill",
                            round_id=state.round_id,
                            priority=72,
                            confidence=0.85,
                            ttl_ms=3000,
                            affected_players=[slot],
                            recommended_inputs=self._inputs([player]),
                            evidence=[f"{slot}_first_kill_of_round"],
                            lifecycle=lifecycle,
                        )
                    )

            if player.round_kills >= 2:
                count = min(player.round_kills, 5)
                lifecycle = self._track(
                    f"multi_kill:{slot}:{count}", now, refresh_ms=None
                )
                if lifecycle is not None:
                    events.append(
                        MatchIntelligenceEvent(
                            type="multi_kill",
                            round_id=state.round_id,
                            priority=MULTI_KILL_PRIORITY[count],
                            confidence=0.9,
                            ttl_ms=4000,
                            affected_players=[slot],
                            recommended_inputs=self._inputs([player]),
                            evidence=[
                                f"{slot}_round_kills={player.round_kills}",
                                f"headshots={player.round_kill_headshots}",
                            ],
                            lifecycle=lifecycle,
                        )
                    )
        return events

    def _defuse_drama(self, state: MatchState, now: float) -> list[MatchIntelligenceEvent]:
        if state.bomb.state != "defusing":
            self._release("defuse_drama")
            return []
        lifecycle = self._track("defuse_drama", now, refresh_ms=1000)
        if lifecycle is None:
            return []
        defuser = (
            state.players.get(state.bomb.player_slot)
            if state.bomb.player_slot
            else None
        )
        heroes = [defuser] if defuser is not None else [
            p for p in state.alive_players() if p.team == "CT"
        ]
        evidence = ["bomb_defusing=true", f"site={state.bomb.site}"]
        if state.bomb.countdown_ms is not None:
            evidence.append(f"countdown_ms={state.bomb.countdown_ms:.0f}")
        return [
            MatchIntelligenceEvent(
                type="defuse_drama",
                round_id=state.round_id,
                priority=95,
                confidence=0.95,
                ttl_ms=2500,
                affected_players=[p.slot for p in heroes],
                recommended_inputs=self._inputs(heroes),
                evidence=evidence,
                lifecycle=lifecycle,
            )
        ]

    def _bomb_last_seconds(
        self, state: MatchState, now: float
    ) -> list[MatchIntelligenceEvent]:
        countdown = state.bomb.countdown_ms
        critical = (
            state.bomb.state in ("planted", "defusing")
            and countdown is not None
            and countdown <= BOMB_CRITICAL_MS
        )
        if not critical:
            self._release("bomb_last_seconds")
            return []
        lifecycle = self._track("bomb_last_seconds", now, refresh_ms=1500)
        if lifecycle is None:
            return []
        # The drama sits with whoever can still change the outcome: the defuser
        # if there is one, otherwise the defenders racing the clock, otherwise
        # the attackers holding the site.
        defuser = (
            state.players.get(state.bomb.player_slot)
            if state.bomb.state == "defusing" and state.bomb.player_slot
            else None
        )
        if defuser is not None:
            heroes = [defuser]
        else:
            heroes = [p for p in state.alive_players() if p.team == "CT"] or [
                p for p in state.alive_players() if p.team == "T"
            ]
        if not heroes:
            return []
        return [
            MatchIntelligenceEvent(
                type="bomb_last_seconds",
                round_id=state.round_id,
                priority=88,
                confidence=0.9,
                ttl_ms=2500,
                affected_players=[p.slot for p in heroes],
                recommended_inputs=self._inputs(heroes),
                evidence=[
                    f"countdown_ms={countdown:.0f}",
                    f"bomb_state={state.bomb.state}",
                ],
                lifecycle=lifecycle,
            )
        ]

    def _potential_clutch(self, state: MatchState, now: float) -> list[MatchIntelligenceEvent]:
        teams = state.alive_by_team()
        for team, players in teams.items():
            enemies = [count for other, count in teams.items() if other != team]
            enemy_alive = sum(len(c) for c in enemies)
            if len(players) == 1 and enemy_alive >= 2:
                hero = players[0]
                key = f"clutch:{hero.slot}"
                lifecycle = self._track(key, now, refresh_ms=1500)
                self._release_others("clutch:", key)
                if lifecycle is None:
                    return []
                bomb_planted = state.bomb.state in ("planted", "defusing")
                confidence = 0.7 + min(0.2, 0.05 * enemy_alive) + (0.05 if bomb_planted else 0)
                evidence = [
                    f"{hero.slot}_last_alive",
                    f"enemy_alive_count={enemy_alive}",
                    f"{hero.slot}_health={hero.health}",
                ]
                if bomb_planted:
                    evidence.append("bomb_planted=true")
                return [
                    MatchIntelligenceEvent(
                        type="potential_clutch",
                        round_id=state.round_id,
                        priority=92,
                        confidence=min(0.99, confidence),
                        ttl_ms=2500,
                        affected_players=[hero.slot],
                        recommended_inputs=self._inputs([hero]),
                        evidence=evidence,
                        lifecycle=lifecycle,
                    )
                ]
        self._release_others("clutch:", None)
        return []

    def _last_alive(self, state: MatchState, now: float) -> list[MatchIntelligenceEvent]:
        teams = state.alive_by_team()
        for team, players in teams.items():
            other_alive = sum(len(c) for o, c in teams.items() if o != team)
            if len(players) == 1 and other_alive >= 2:
                hero = players[0]
                key = f"last_alive:{hero.slot}"
                lifecycle = self._track(key, now, refresh_ms=2000)
                self._release_others("last_alive:", key)
                if lifecycle is None:
                    return []
                return [
                    MatchIntelligenceEvent(
                        type="last_alive",
                        round_id=state.round_id,
                        priority=80,
                        confidence=0.9,
                        ttl_ms=3000,
                        affected_players=[hero.slot],
                        recommended_inputs=self._inputs([hero]),
                        evidence=[f"{hero.slot}_last_alive_for_{team}"],
                        lifecycle=lifecycle,
                    )
                ]
        self._release_others("last_alive:", None)
        return []

    def _site_execute(self, state: MatchState, now: float) -> list[MatchIntelligenceEvent]:
        attackers = [
            player
            for player in state.alive_players()
            if player.team == "T" and player.active_weapon in GRENADE_WEAPONS
        ]
        if len(attackers) < 2:
            self._release("site_execute")
            return []
        lifecycle = self._track("site_execute", now, refresh_ms=2000)
        if lifecycle is None:
            return []
        return [
            MatchIntelligenceEvent(
                type="site_execute",
                round_id=state.round_id,
                priority=75,
                confidence=0.6 + min(0.3, 0.1 * len(attackers)),
                ttl_ms=2500,
                affected_players=[player.slot for player in attackers],
                recommended_inputs=self._inputs(attackers),
                evidence=[f"attackers_using_utility={len(attackers)}"],
                lifecycle=lifecycle,
            )
        ]

    def _retake_started(self, state: MatchState, now: float) -> list[MatchIntelligenceEvent]:
        if state.bomb.state != "planted":
            self._release("retake_started")
            return []
        defenders = [p for p in state.alive_players() if p.team == "CT"]
        if not defenders:
            self._release("retake_started")
            return []
        lifecycle = self._track("retake_started", now, refresh_ms=2500)
        if lifecycle is None:
            return []
        return [
            MatchIntelligenceEvent(
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
                lifecycle=lifecycle,
            )
        ]

    def _awp_duel(self, state: MatchState, now: float) -> list[MatchIntelligenceEvent]:
        snipers = [
            player
            for player in state.alive_players()
            if player.active_weapon in SNIPER_WEAPONS
        ]
        teams = {player.team for player in snipers}
        if len(snipers) < 2 or len(teams) < 2:
            self._release("awp_duel")
            return []
        lifecycle = self._track("awp_duel", now, refresh_ms=2000)
        if lifecycle is None:
            return []
        return [
            MatchIntelligenceEvent(
                type="awp_duel",
                round_id=state.round_id,
                priority=82,
                confidence=0.75,
                ttl_ms=2000,
                affected_players=[player.slot for player in snipers],
                recommended_inputs=self._inputs(snipers),
                evidence=[f"snipers={[p.slot for p in snipers]}"],
                lifecycle=lifecycle,
            )
        ]

    def _eco_upset_risk(self, state: MatchState, now: float) -> list[MatchIntelligenceEvent]:
        teams: dict[str, list[PlayerState]] = {}
        for player in state.alive_players():
            teams.setdefault(player.team, []).append(player)
        if len(teams) < 2:
            self._release("eco_upset_risk")
            return []
        money = {
            team: statistics.mean([p.money for p in players]) if players else 0
            for team, players in teams.items()
        }
        eco_team = min(money, key=money.get)
        rich_team = max(money, key=money.get)
        if not (
            money[eco_team] <= ECO_MONEY and len(teams[eco_team]) > len(teams[rich_team])
        ):
            self._release("eco_upset_risk")
            return []
        lifecycle = self._track("eco_upset_risk", now, refresh_ms=4000)
        if lifecycle is None:
            return []
        return [
            MatchIntelligenceEvent(
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
                lifecycle=lifecycle,
            )
        ]

    # -- helpers -------------------------------------------------------------
    def _track(self, key: str, now: float, refresh_ms: float | None) -> str | None:
        """Sustained-story gate.

        First call while the condition holds returns "start". Later calls
        return "update" once `refresh_ms` has elapsed since the last emit, or
        None while inside the refresh window. `refresh_ms=None` means the story
        is strictly one-shot per condition span (per round for keys that are
        never released).
        """
        last = self._active.get(key)
        if last is None:
            self._active[key] = now
            return "start"
        if refresh_ms is not None and now - last >= refresh_ms:
            self._active[key] = now
            return "update"
        return None

    def _release(self, key: str) -> None:
        self._active.pop(key, None)

    def _release_others(self, prefix: str, keep: str | None) -> None:
        """Release every active story under `prefix` except `keep`."""
        for key in list(self._active):
            if key.startswith(prefix) and key != keep:
                self._active.pop(key, None)

    def _inputs(self, players: list[PlayerState]) -> list[str]:
        return [player.input_id or player.slot for player in players]
