from __future__ import annotations

import logging

from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import now_ms
from excalibur.matrix.domain import (
    BombState,
    GameStateEvent,
    MatchState,
    PlayerState,
)
from excalibur.matrix.latency import LatencyBudget

logger = logging.getLogger(__name__)

SNIPER_WEAPONS = {"weapon_awp", "weapon_ssg08", "weapon_scar20", "weapon_g3sg1"}
GRENADE_WEAPONS = {
    "weapon_hegrenade",
    "weapon_flashbang",
    "weapon_smokegrenade",
    "weapon_molotov",
    "weapon_incgrenade",
    "weapon_decoy",
}
LOW_HEALTH = 35
# GSI reports flash intensity 0-255; above this the player is effectively blind.
FULL_FLASH = 200


class GsiReceiver:
    """Normalizes CS2 Game State Integration payloads.

    Converts raw GSI JSON into a `MatchState`, diffs against the previous state
    to emit `GameStateEvent`s, and publishes both to the Game State Bus. This is
    the truth layer for facts; agents consume it instead of inferring from
    pixels.
    """

    def __init__(
        self,
        buses: MatrixBuses,
        budget: LatencyBudget | None = None,
        steam_to_input: dict[str, str] | None = None,
    ) -> None:
        self.buses = buses
        self.budget = budget or LatencyBudget()
        self.steam_to_input = steam_to_input or {}
        self.state: MatchState | None = None
        self._version = 0
        self._last_gsi_age_ms: float = 0.0

    def ingest(self, payload: dict) -> MatchState:
        received_at = now_ms()
        state = self._normalize(payload, received_at)
        events = self._diff(self.state, state)
        previous = self.state
        self.state = state

        provider_ts = float(payload.get("provider", {}).get("timestamp", 0)) * 1000.0
        if provider_ts:
            self._last_gsi_age_ms = max(0.0, received_at - provider_ts)

        for event in events:
            self.buses.game_state.publish(event)
        logger.debug(
            "gsi ingest round=%s phase=%s players=%d events=%d",
            state.round_id,
            state.phase,
            len(state.players),
            len(events),
        )
        _ = previous
        return state

    def is_fresh(self, now: float | None = None) -> bool:
        if self.state is None:
            return False
        return self.state.age_ms(now) <= self.budget.max_state_age_ms

    def _normalize(self, payload: dict, received_at: float) -> MatchState:
        self._version += 1
        map_block = payload.get("map", {}) or {}
        round_block = payload.get("round", {}) or {}
        map_name = map_block.get("name")
        round_number = int(map_block.get("round", 0) or 0)
        round_id = f"{map_name or 'map'}_round_{round_number:02d}"
        phase = round_block.get("phase") or map_block.get("phase") or "unknown"

        players: dict[str, PlayerState] = {}
        allplayers = payload.get("allplayers")
        if allplayers:
            for steamid, raw in allplayers.items():
                player = self._normalize_player(steamid, raw)
                players[player.slot] = player
        else:
            single = payload.get("player")
            if single:
                steamid = single.get("steamid", "self")
                player = self._normalize_player(steamid, single)
                players[player.slot] = player

        bomb = self._normalize_bomb(payload, round_block)

        return MatchState(
            round_id=round_id,
            phase=phase,
            players=players,
            bomb=bomb,
            round_number=round_number,
            map_name=map_name,
            state_version=self._version,
            game_timestamp=float(payload.get("provider", {}).get("timestamp", 0) or 0),
            received_at=received_at,
        )

    def _normalize_player(self, steamid: str, raw: dict) -> PlayerState:
        state_block = raw.get("state", {}) or {}
        weapons_block = raw.get("weapons", {}) or {}
        weapons: list[str] = []
        active_weapon: str | None = None
        for weapon in weapons_block.values():
            name = weapon.get("name")
            if not name:
                continue
            weapons.append(name)
            if weapon.get("state") == "active":
                active_weapon = name

        match_stats = raw.get("match_stats", {}) or {}
        slot = self.steam_to_input.get(steamid, steamid)
        return PlayerState(
            slot=slot,
            name=raw.get("name", slot),
            team=raw.get("team", "UNK"),
            health=int(state_block.get("health", 100)),
            armor=int(state_block.get("armor", 0)),
            alive=int(state_block.get("health", 100)) > 0,
            flashed=int(state_block.get("flashed", 0)),
            burning=int(state_block.get("burning", 0)),
            active_weapon=active_weapon,
            weapons=weapons,
            money=int(state_block.get("money", 0)),
            round_kills=int(state_block.get("round_kills", 0)),
            round_kill_headshots=int(state_block.get("round_killhs", 0)),
            match_kills=int(match_stats.get("kills", 0)),
            input_id=self.steam_to_input.get(steamid),
        )

    def _normalize_bomb(self, payload: dict, round_block: dict) -> BombState:
        bomb_block = payload.get("bomb")
        if bomb_block:
            bomb_player = bomb_block.get("player")
            return BombState(
                state=bomb_block.get("state", "carried"),
                site=bomb_block.get("site"),
                countdown_ms=(
                    float(bomb_block["countdown"]) * 1000.0
                    if bomb_block.get("countdown") is not None
                    else None
                ),
                player_slot=(
                    self.steam_to_input.get(str(bomb_player), str(bomb_player))
                    if bomb_player
                    else None
                ),
            )
        if round_block.get("bomb"):
            return BombState(state=round_block["bomb"])
        return BombState()

    def _diff(self, old: MatchState | None, new: MatchState) -> list[GameStateEvent]:
        events: list[GameStateEvent] = []

        if old is None or old.round_id != new.round_id:
            events.append(
                GameStateEvent(type="round_started", data={"phase": new.phase}, round_id=new.round_id)
            )
        elif old.phase != new.phase:
            events.append(
                GameStateEvent(
                    type="round_phase_changed",
                    data={"from": old.phase, "to": new.phase},
                    round_id=new.round_id,
                )
            )

        old_players = old.players if old and old.round_id == new.round_id else {}
        for slot, player in new.players.items():
            prev = old_players.get(slot)

            if (prev is None or prev.alive) and not player.alive:
                events.append(
                    GameStateEvent(
                        type="player_death",
                        player_slot=slot,
                        data={"team": player.team},
                        round_id=new.round_id,
                    )
                )

            if player.alive and player.health <= LOW_HEALTH and (
                prev is None or prev.health > LOW_HEALTH
            ):
                events.append(
                    GameStateEvent(
                        type="player_low_health",
                        player_slot=slot,
                        data={"health": player.health},
                        round_id=new.round_id,
                    )
                )

            sniper_now = player.active_weapon in SNIPER_WEAPONS
            sniper_before = prev is not None and prev.active_weapon in SNIPER_WEAPONS
            if sniper_now and not sniper_before:
                events.append(
                    GameStateEvent(
                        type="sniper_equipped",
                        player_slot=slot,
                        data={"weapon": player.active_weapon},
                        round_id=new.round_id,
                    )
                )

            grenade_now = player.active_weapon in GRENADE_WEAPONS
            grenade_before = prev is not None and prev.active_weapon in GRENADE_WEAPONS
            if grenade_now and not grenade_before:
                events.append(
                    GameStateEvent(
                        type="grenade_equipped",
                        player_slot=slot,
                        data={"weapon": player.active_weapon},
                        round_id=new.round_id,
                    )
                )

            if player.burning and (prev is None or not prev.burning):
                events.append(
                    GameStateEvent(
                        type="player_burning",
                        player_slot=slot,
                        data={},
                        round_id=new.round_id,
                    )
                )

            if player.flashed >= FULL_FLASH and (
                prev is None or prev.flashed < FULL_FLASH
            ):
                events.append(
                    GameStateEvent(
                        type="player_flashed",
                        player_slot=slot,
                        data={"flashed": player.flashed},
                        round_id=new.round_id,
                    )
                )

            # Kills are the strongest cut signal a broadcast has. The event
            # carries the KILLER's slot (unlike player_death, which carries the
            # victim) so the director can cut to the player who just earned the
            # moment.
            prev_kills = prev.round_kills if prev is not None else 0
            if player.round_kills > prev_kills:
                prev_hs = prev.round_kill_headshots if prev is not None else 0
                events.append(
                    GameStateEvent(
                        type="player_kill",
                        player_slot=slot,
                        data={
                            "round_kills": player.round_kills,
                            "new_kills": player.round_kills - prev_kills,
                            "headshot": player.round_kill_headshots > prev_hs,
                            "weapon": player.active_weapon,
                            "team": player.team,
                        },
                        round_id=new.round_id,
                    )
                )

        old_bomb = old.bomb.state if old and old.round_id == new.round_id else None
        if new.bomb.state != old_bomb and new.bomb.state in (
            "planted",
            "defusing",
            "defused",
            "exploded",
        ):
            events.append(
                GameStateEvent(
                    type=f"bomb_{new.bomb.state}",
                    player_slot=new.bomb.player_slot,
                    data={"site": new.bomb.site},
                    round_id=new.round_id,
                )
            )

        return events
