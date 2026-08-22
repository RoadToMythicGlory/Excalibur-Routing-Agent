from __future__ import annotations

import pytest

from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import set_now_override
from excalibur.matrix.gsi.receiver import GsiReceiver
from excalibur.matrix.gsi.simulator import ROSTER, clutch_round_sequence


@pytest.fixture(autouse=True)
def fixed_clock():
    set_now_override(1_700_000_000_000.0)
    yield
    set_now_override(None)


def test_normalizes_allplayers_into_match_state():
    buses = MatrixBuses()
    receiver = GsiReceiver(buses)
    payloads = list(clutch_round_sequence())
    state = receiver.ingest(payloads[1])  # live with two AWPs
    assert len(state.players) == len(ROSTER)
    awps = [p for p in state.players.values() if p.active_weapon == "weapon_awp"]
    assert len(awps) == 2


def test_diff_emits_game_state_events():
    buses = MatrixBuses()
    received: list[str] = []
    buses.game_state.subscribe(lambda event: received.append(event.type))
    receiver = GsiReceiver(buses)
    for payload in clutch_round_sequence():
        receiver.ingest(payload)

    assert "round_started" in received
    assert "sniper_equipped" in received
    assert "player_death" in received
    assert "player_low_health" in received
    assert "bomb_planted" in received
    assert "player_kill" in received
    assert "bomb_defusing" in received


def test_kill_event_names_the_killer_with_totals():
    mapping = {steamid: f"input_{i + 1:02d}" for i, (steamid, _, _) in enumerate(ROSTER)}
    buses = MatrixBuses()
    kills = []
    buses.game_state.subscribe(
        lambda event: kills.append(event) if event.type == "player_kill" else None
    )
    receiver = GsiReceiver(buses, steam_to_input=mapping)
    for payload in clutch_round_sequence():
        receiver.ingest(payload)

    # The entry AWPer (ROSTER[5] -> input_06) jumps from 0 to 2 kills.
    double = next(k for k in kills if k.player_slot == "input_06")
    assert double.data["round_kills"] == 2
    assert double.data["new_kills"] == 2
    assert double.data["headshot"] is True


def test_defusing_bomb_carries_defuser_slot():
    mapping = {ROSTER[0][0]: "input_01"}
    buses = MatrixBuses()
    receiver = GsiReceiver(buses, steam_to_input=mapping)
    state = None
    for payload in clutch_round_sequence():
        state = receiver.ingest(payload)

    assert state is not None
    assert state.bomb.state == "defusing"
    assert state.bomb.player_slot == "input_01"


def test_steam_to_input_mapping_sets_input_id():
    mapping = {ROSTER[0][0]: "input_01"}
    buses = MatrixBuses()
    receiver = GsiReceiver(buses, steam_to_input=mapping)
    state = receiver.ingest(list(clutch_round_sequence())[1])
    hero = state.players["input_01"]
    assert hero.input_id == "input_01"


def test_fresh_then_stale_state():
    buses = MatrixBuses()
    receiver = GsiReceiver(buses)
    receiver.ingest(list(clutch_round_sequence())[0])
    assert receiver.is_fresh()
    set_now_override(1_700_000_000_000.0 + 5_000.0)
    assert not receiver.is_fresh()
