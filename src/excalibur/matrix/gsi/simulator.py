from __future__ import annotations

from typing import Iterator

"""Deterministic CS2 GSI payload sequences for development and tests.

Produces payloads in the same shape CS2 posts for an observer/GOTV client
(`allplayers` keyed by steamid) so the receiver, game-state bus, and match
intelligence engine can be exercised without a live game.
"""

# Neutral placeholder roster for the simulator only. Real names come from
# operator configuration or live GSI; nothing here ships a real identity.
ROSTER = [
    ("76561190000000001", "CT 1", "CT"),
    ("76561190000000002", "CT 2", "CT"),
    ("76561190000000003", "CT 3", "CT"),
    ("76561190000000004", "CT 4", "CT"),
    ("76561190000000005", "CT 5", "CT"),
    ("76561190000000006", "T 1", "T"),
    ("76561190000000007", "T 2", "T"),
    ("76561190000000008", "T 3", "T"),
    ("76561190000000009", "T 4", "T"),
    ("76561190000000010", "T 5", "T"),
]


def _player(
    name: str,
    team: str,
    health: int,
    weapon: str,
    money: int = 4000,
    round_kills: int = 0,
    round_killhs: int = 0,
) -> dict:
    weapons = {
        "weapon_0": {"name": "weapon_knife", "type": "Knife", "state": "holstered"},
        "weapon_1": {"name": weapon, "type": "Rifle", "state": "active"},
    }
    return {
        "name": name,
        "team": team,
        "state": {"health": health, "armor": 100 if health > 0 else 0, "money": money,
                  "flashed": 0, "burning": 0,
                  "round_kills": round_kills, "round_killhs": round_killhs},
        "match_stats": {"kills": round_kills, "deaths": 0},
        "weapons": weapons,
    }


def _payload(
    round_number: int,
    phase: str,
    timestamp: float,
    overrides: dict[str, dict],
    bomb: dict | None = None,
) -> dict:
    allplayers: dict[str, dict] = {}
    for steamid, name, team in ROSTER:
        base = _player(name, team, 100, "weapon_ak47" if team == "T" else "weapon_m4a1")
        if steamid in overrides:
            base = {**base, **overrides[steamid]}
        allplayers[steamid] = base
    payload = {
        "provider": {"name": "Counter-Strike 2", "timestamp": timestamp},
        "map": {"name": "de_mirage", "round": round_number, "phase": "live"},
        "round": {"phase": phase},
        "allplayers": allplayers,
    }
    if bomb is not None:
        payload["bomb"] = bomb
    return payload


def clutch_round_sequence(start_ts: float = 1_700_000_000.0) -> Iterator[dict]:
    """A round that builds toward a 1vN clutch with an AWP duel and bomb plant."""

    ts = start_ts

    # Freezetime.
    yield _payload(18, "freezetime", ts, {})
    ts += 1

    # Live: a CT and a T pick AWPs (sets up an AWP duel).
    yield _payload(
        18,
        "live",
        ts,
        {
            ROSTER[0][0]: _player(ROSTER[0][1], "CT", 100, "weapon_awp"),
            ROSTER[5][0]: _player(ROSTER[5][1], "T", 100, "weapon_awp"),
        },
    )
    ts += 1

    # A T throws a molotov / utility toward a site (site execute building).
    yield _payload(
        18,
        "live",
        ts,
        {
            ROSTER[5][0]: _player(ROSTER[5][1], "T", 100, "weapon_molotov"),
            ROSTER[6][0]: _player(ROSTER[6][1], "T", 100, "weapon_smokegrenade"),
            ROSTER[7][0]: _player(ROSTER[7][1], "T", 100, "weapon_flashbang"),
        },
    )
    ts += 1

    # Trades happen: four CTs die, leaving one CT alive (last alive / clutch).
    # The entry AWPer picks up a double kill (multi_kill story) and teammates
    # trade the rest.
    dead = {steamid: _player(name, "CT", 0, "weapon_m4a1")
            for steamid, name, team in ROSTER[1:5]}
    killers = {
        ROSTER[5][0]: _player(
            ROSTER[5][1], "T", 100, "weapon_awp", round_kills=2, round_killhs=1
        ),
        ROSTER[6][0]: _player(ROSTER[6][1], "T", 80, "weapon_ak47", round_kills=1),
        ROSTER[7][0]: _player(ROSTER[7][1], "T", 65, "weapon_ak47", round_kills=1),
    }
    yield _payload(
        18,
        "live",
        ts,
        {
            ROSTER[0][0]: _player(ROSTER[0][1], "CT", 42, "weapon_awp"),
            **dead,
            **killers,
        },
        bomb={"state": "planted", "site": "A", "countdown": 28.0},
    )
    ts += 1

    # Clutch deepens: lone CT low health, three T alive.
    yield _payload(
        18,
        "live",
        ts,
        {
            ROSTER[0][0]: _player(ROSTER[0][1], "CT", 18, "weapon_awp"),
            **dead,
            **killers,
        },
        bomb={"state": "planted", "site": "A", "countdown": 14.0},
    )
    ts += 1

    # The hero sticks the defuse with the clock nearly out (defuse drama +
    # bomb last-seconds story).
    yield _payload(
        18,
        "live",
        ts,
        {
            ROSTER[0][0]: _player(ROSTER[0][1], "CT", 18, "weapon_awp"),
            **dead,
            **killers,
        },
        bomb={
            "state": "defusing",
            "site": "A",
            "countdown": 7.0,
            "player": ROSTER[0][0],
        },
    )


def collect(sequence: Iterator[dict]) -> list[dict]:
    return list(sequence)
