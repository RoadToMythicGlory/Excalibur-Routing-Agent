from __future__ import annotations

from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import advance_now_override, set_now_override
from excalibur.matrix.soccer.config import load_soccer_profile
from excalibur.matrix.soccer.domain import (
    EventLifecycle,
    SoccerObservationKind,
    SourceAuthority,
    VisionObservation,
)
from excalibur.matrix.soccer.fusion import SoccerFusion
from excalibur.matrix.soccer.receiver import SoccerReceiver
from excalibur.matrix.soccer.simulator import event, heartbeat


def _stack() -> tuple[SoccerReceiver, SoccerFusion]:
    buses = MatrixBuses()
    profile = load_soccer_profile()
    return SoccerReceiver(buses, profile), SoccerFusion(buses, profile)


def test_foul_transitions_to_free_kick_then_ends():
    set_now_override(1_000)
    receiver, fusion = _stack()
    fusion.ingest_structured(receiver.ingest(heartbeat(sequence=1)))
    fusion.ingest_structured(
        receiver.ingest(event("foul", event_id="f1", sequence=2, correlation_id="sp"))
    )
    advance_now_override(400)
    view = fusion.tick()
    assert view.event is not None
    assert view.event.lifecycle == EventLifecycle.ACTIVE.value
    fusion.ingest_structured(
        receiver.ingest(
            event(
                "free_kick",
                revision="update",
                event_id="fk",
                sequence=3,
                correlation_id="sp",
            )
        )
    )
    assert fusion.event is not None
    assert fusion.event.event_type == "free_kick"
    fusion.ingest_structured(
        receiver.ingest(
            event(
                "free_kick",
                revision="end",
                event_id="fk",
                sequence=4,
                correlation_id="sp",
            )
        )
    )
    advance_now_override(900)
    view = fusion.tick()
    assert view.event is None
    set_now_override(None)


def test_ordinary_vision_pass_does_not_create_event():
    set_now_override(1_000)
    _, fusion = _stack()
    fusion.ingest_vision(
        VisionObservation(
            kind=SoccerObservationKind.ORDINARY_PASS.value,
            confidence=0.99,
            evidence=["pass"],
        )
    )
    assert fusion.tick().event is None
    set_now_override(None)


def test_vision_used_only_when_structured_is_stale():
    set_now_override(1_000)
    receiver, fusion = _stack()
    fusion.ingest_structured(receiver.ingest(heartbeat(sequence=1)))
    fusion.ingest_vision(
        VisionObservation(
            kind=SoccerObservationKind.EVENT.value,
            event_type="penalty_kick",
            confidence=0.95,
            phase="setup",
        )
    )
    assert fusion.view().authority == SourceAuthority.STRUCTURED.value
    assert fusion.event is None
    advance_now_override(3_000)
    fusion.ingest_vision(
        VisionObservation(
            kind=SoccerObservationKind.EVENT.value,
            event_type="penalty_kick",
            confidence=0.95,
            phase="setup",
        )
    )
    advance_now_override(400)
    view = fusion.tick()
    assert view.authority == SourceAuthority.VISION.value
    assert view.event is not None
    assert view.event.event_type == "penalty_kick"
    set_now_override(None)


def test_foul_transitions_to_penalty_kick():
    set_now_override(1_000)
    receiver, fusion = _stack()
    fusion.ingest_structured(receiver.ingest(heartbeat(sequence=1)))
    fusion.ingest_structured(
        receiver.ingest(event("foul", event_id="f1", sequence=2, correlation_id="box"))
    )
    advance_now_override(400)
    fusion.tick()
    fusion.ingest_structured(
        receiver.ingest(
            event(
                "penalty_kick",
                revision="update",
                event_id="pen",
                sequence=3,
                correlation_id="box",
            )
        )
    )
    assert fusion.event is not None
    assert fusion.event.event_type == "penalty_kick"
    assert fusion.event.lifecycle == EventLifecycle.ACTIVE.value
    set_now_override(None)


def test_low_confidence_structured_event_is_ignored():
    set_now_override(1_000)
    receiver, fusion = _stack()
    fusion.ingest_structured(receiver.ingest(heartbeat(sequence=1)))
    fusion.ingest_structured(
        receiver.ingest(event("foul", sequence=2, confidence=0.2))
    )
    assert fusion.event is None
    set_now_override(None)


def test_shadow_vision_never_gains_authority_or_creates_events():
    set_now_override(1_000)
    receiver, fusion = _stack()
    fusion.vision_authority_allowed = False
    fusion.ingest_structured(receiver.ingest(heartbeat(sequence=1)))
    advance_now_override(3_000)
    fusion.ingest_vision(
        VisionObservation(
            kind=SoccerObservationKind.EVENT.value,
            event_type="penalty_kick",
            confidence=0.95,
        )
    )
    view = fusion.view()
    assert view.vision_fresh is True
    assert view.authority == SourceAuthority.NONE.value
    assert view.fallback_reason == "vision_shadow"
    assert fusion.event is None
    set_now_override(None)


def test_source_loss_ends_active_event():
    set_now_override(1_000)
    receiver, fusion = _stack()
    fusion.ingest_structured(receiver.ingest(event("foul", sequence=1)))
    advance_now_override(400)
    fusion.tick()
    assert fusion.event is not None
    assert fusion.event.lifecycle == EventLifecycle.ACTIVE.value
    advance_now_override(2_600)
    fusion.tick()
    assert fusion.event is not None
    assert fusion.event.lifecycle == EventLifecycle.RESOLVING.value
    assert "source_lost" in fusion.event.evidence
    advance_now_override(900)
    assert fusion.tick().event is None
    set_now_override(None)


def test_event_timeout_closes():
    set_now_override(1_000)
    receiver, fusion = _stack()
    fusion.ingest_structured(receiver.ingest(event("burst_attack", sequence=1)))
    advance_now_override(400)
    view = fusion.tick()
    assert view.event is not None
    assert view.event.lifecycle == EventLifecycle.ACTIVE.value
    advance_now_override(30_000)
    fusion.tick()
    assert fusion.event is not None
    assert fusion.event.lifecycle == EventLifecycle.RESOLVING.value
    assert "max_duration" in fusion.event.evidence
    advance_now_override(900)
    view = fusion.tick()
    assert view.event is None
    set_now_override(None)
