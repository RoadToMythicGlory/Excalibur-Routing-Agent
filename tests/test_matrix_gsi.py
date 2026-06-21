from __future__ import annotations

import pytest

from obs_auto_director.matrix.buses import MatrixBuses
from obs_auto_director.matrix.clock import set_now_override
from obs_auto_director.matrix.gsi.receiver import GsiReceiver
from obs_auto_director.matrix.gsi.simulator import ROSTER, clutch_round_sequence


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
