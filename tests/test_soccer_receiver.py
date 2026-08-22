from __future__ import annotations

from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import set_now_override
from excalibur.matrix.soccer.config import load_soccer_profile
from excalibur.matrix.soccer.receiver import SoccerReceiver
from excalibur.matrix.soccer.simulator import event, heartbeat, ordinary_pass_payload


def _receiver() -> SoccerReceiver:
    return SoccerReceiver(MatrixBuses(), load_soccer_profile())


def test_receiver_accepts_heartbeat_and_event(monkeypatch):
    set_now_override(10_000)
    receiver = _receiver()
    assert receiver.ingest(heartbeat(sequence=1)) is not None
    assert receiver.ingest(event("corner_kick", sequence=2)) is not None
    assert receiver.is_fresh()
    assert receiver.rejected == 0
    set_now_override(None)


def test_receiver_rejects_out_of_order_and_future():
    set_now_override(10_000)
    receiver = _receiver()
    receiver.ingest(heartbeat(sequence=4))
    assert receiver.ingest(event("foul", sequence=3)) is None
    future = heartbeat(sequence=5, timestamp_ms=50_000)
    assert receiver.ingest(future) is None
    assert receiver.rejected >= 2
    set_now_override(None)


def test_receiver_rejects_stale_payload():
    set_now_override(10_000)
    receiver = _receiver()
    assert receiver.ingest(heartbeat(sequence=1, timestamp_ms=5_000)) is None
    assert receiver.rejected == 1
    assert receiver.is_fresh() is False
    set_now_override(None)


def test_ordinary_pass_is_not_an_event():
    receiver = _receiver()
    assert receiver.ingest(ordinary_pass_payload()) is None
