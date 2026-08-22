from __future__ import annotations

from excalibur.matrix.clock import now_ms
from excalibur.matrix.soccer.domain import SoccerEventType, SoccerPhase, SoccerRevision


def heartbeat(match_id: str = "sim-soccer", sequence: int = 1, **extra) -> dict:
    payload = {
        "kind": "heartbeat",
        "match_id": match_id,
        "timestamp_ms": now_ms(),
        "sequence": sequence,
        "period": 1,
        "clock_mmss": "10:00",
        "home_score": 0,
        "away_score": 0,
        "source": "structured",
        "confidence": 1.0,
    }
    payload.update(extra)
    return payload


def event(
    event_type: str,
    revision: str = SoccerRevision.START.value,
    event_id: str = "evt-1",
    match_id: str = "sim-soccer",
    sequence: int = 2,
    **extra,
) -> dict:
    payload = {
        "kind": "event",
        "revision": revision,
        "event_id": event_id,
        "correlation_id": extra.pop("correlation_id", event_id),
        "match_id": match_id,
        "event_type": event_type,
        "phase": extra.pop("phase", SoccerPhase.SETUP.value),
        "timestamp_ms": extra.pop("timestamp_ms", now_ms()),
        "sequence": sequence,
        "confidence": extra.pop("confidence", 0.9),
        "source": extra.pop("source", "structured"),
        "team": extra.pop("team", "home"),
        "zone": extra.pop("zone", "attacking_third"),
        "evidence": extra.pop("evidence", [event_type]),
    }
    payload.update(extra)
    return payload


def ordinary_pass_payload(sequence: int = 2) -> dict:
    """A pass is not a soccer director event; tests use this as a negative case."""
    return {
        "kind": "observation",
        "observation": "ordinary_pass",
        "match_id": "sim-soccer",
        "timestamp_ms": now_ms(),
        "sequence": sequence,
        "confidence": 0.99,
    }


def foul_to_free_kick() -> list[dict]:
    return [
        heartbeat(sequence=1),
        event(SoccerEventType.FOUL.value, event_id="foul-1", sequence=2, correlation_id="sp-1"),
        event(
            SoccerEventType.FREE_KICK.value,
            revision=SoccerRevision.UPDATE.value,
            event_id="fk-1",
            sequence=3,
            correlation_id="sp-1",
            phase=SoccerPhase.SETUP.value,
        ),
        event(
            SoccerEventType.FREE_KICK.value,
            revision=SoccerRevision.UPDATE.value,
            event_id="fk-1",
            sequence=4,
            correlation_id="sp-1",
            phase=SoccerPhase.EXECUTION.value,
        ),
        event(
            SoccerEventType.FREE_KICK.value,
            revision=SoccerRevision.END.value,
            event_id="fk-1",
            sequence=5,
            correlation_id="sp-1",
            phase=SoccerPhase.FOLLOW_THROUGH.value,
        ),
    ]


def penalty_sequence() -> list[dict]:
    return [
        heartbeat(sequence=1),
        event(SoccerEventType.PENALTY_KICK.value, event_id="pen-1", sequence=2),
        event(
            SoccerEventType.PENALTY_KICK.value,
            revision=SoccerRevision.UPDATE.value,
            event_id="pen-1",
            sequence=3,
            phase=SoccerPhase.EXECUTION.value,
        ),
        event(
            SoccerEventType.PENALTY_KICK.value,
            revision=SoccerRevision.END.value,
            event_id="pen-1",
            sequence=4,
            phase=SoccerPhase.FOLLOW_THROUGH.value,
        ),
    ]


def corner_sequence() -> list[dict]:
    return [
        heartbeat(sequence=1),
        event(
            SoccerEventType.CORNER_KICK.value,
            event_id="crn-1",
            sequence=2,
            zone="left_corner",
        ),
        event(
            SoccerEventType.CORNER_KICK.value,
            revision=SoccerRevision.UPDATE.value,
            event_id="crn-1",
            sequence=3,
            phase=SoccerPhase.EXECUTION.value,
            zone="attacking_box",
        ),
        event(
            SoccerEventType.CORNER_KICK.value,
            revision=SoccerRevision.END.value,
            event_id="crn-1",
            sequence=4,
        ),
    ]


def burst_attack_sequence() -> list[dict]:
    return [
        heartbeat(sequence=1),
        event(
            SoccerEventType.BURST_ATTACK.value,
            event_id="brk-1",
            sequence=2,
            zone="midfield",
            phase=SoccerPhase.SETUP.value,
        ),
        event(
            SoccerEventType.BURST_ATTACK.value,
            revision=SoccerRevision.UPDATE.value,
            event_id="brk-1",
            sequence=3,
            phase=SoccerPhase.EXECUTION.value,
            zone="attacking_third",
        ),
        event(
            SoccerEventType.BURST_ATTACK.value,
            revision=SoccerRevision.UPDATE.value,
            event_id="brk-1",
            sequence=4,
            phase=SoccerPhase.FOLLOW_THROUGH.value,
            zone="attacking_box",
        ),
        event(
            SoccerEventType.BURST_ATTACK.value,
            revision=SoccerRevision.END.value,
            event_id="brk-1",
            sequence=5,
        ),
    ]
