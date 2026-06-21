from __future__ import annotations

from typing import Iterator

"""Deterministic CS2 GSI payload sequences for development and tests.

Produces payloads in the same shape CS2 posts for an observer/GOTV client
(`allplayers` keyed by steamid) so the receiver, game-state bus, and match
intelligence engine can be exercised without a live game.
"""

ROSTER = [
    ("76561190000000001", "ZywOo", "CT"),
    ("76561190000000002", "apEX", "CT"),
    ("76561190000000003", "Spinx", "CT"),
    ("76561190000000004", "flameZ", "CT"),
    ("76561190000000005", "mezii", "CT"),
    ("76561190000000006", "donk", "T"),
    ("76561190000000007", "sh1ro", "T"),
    ("76561190000000008", "magixx", "T"),
    ("76561190000000009", "zont1x", "T"),
    ("76561190000000010", "chopper", "T"),
]


def _player(name: str, team: str, health: int, weapon: str, money: int = 4000) -> dict:
    weapons = {
        "weapon_0": {"name": "weapon_knife", "type": "Knife", "state": "holstered"},
        "weapon_1": {"name": weapon, "type": "Rifle", "state": "active"},
    }
    return {
        "name": name,
        "team": team,
        "state": {"health": health, "armor": 100 if health > 0 else 0, "money": money,
                  "flashed": 0, "burning": 0},
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
            ROSTER[0][0]: _player("ZywOo", "CT", 100, "weapon_awp"),
            ROSTER[5][0]: _player("donk", "T", 100, "weapon_awp"),
        },
    )
    ts += 1

    # A T throws a molotov / utility toward a site (site execute building).
    yield _payload(
        18,
        "live",
        ts,
        {
            ROSTER[5][0]: _player("donk", "T", 100, "weapon_molotov"),
            ROSTER[6][0]: _player("sh1ro", "T", 100, "weapon_smokegrenade"),
            ROSTER[7][0]: _player("magixx", "T", 100, "weapon_flashbang"),
        },
    )
    ts += 1

    # Trades happen: four CTs die, leaving one CT alive (last alive / clutch).
    dead = {steamid: _player(name, "CT", 0, "weapon_m4a1")
            for steamid, name, team in ROSTER[1:5]}
    yield _payload(
        18,
        "live",
        ts,
        {
            ROSTER[0][0]: _player("ZywOo", "CT", 42, "weapon_awp"),
            **dead,
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
            ROSTER[0][0]: _player("ZywOo", "CT", 18, "weapon_awp"),
            **dead,
        },
        bomb={"state": "planted", "site": "A", "countdown": 14.0},
    )


def collect(sequence: Iterator[dict]) -> list[dict]:
    return list(sequence)
