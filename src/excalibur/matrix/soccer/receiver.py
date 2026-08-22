from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import now_ms
from excalibur.matrix.domain import GameStateEvent
from excalibur.matrix.soccer.config import SoccerProfile
from excalibur.matrix.soccer.domain import (
    PitchPoint,
    SoccerEventRevision,
    SoccerHeartbeat,
    SoccerObservationKind,
)

logger = logging.getLogger(__name__)


class _XyModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    x: float = 0.5
    y: float = 0.5


class HeartbeatPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str
    match_id: str
    timestamp_ms: float
    sequence: int
    period: int | None = None
    clock_mmss: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    source: str = "structured"
    confidence: float = 1.0


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str = "event"
    revision: str
    event_id: str
    match_id: str
    event_type: str
    timestamp_ms: float
    sequence: int
    confidence: float
    correlation_id: str | None = None
    phase: str = "setup"
    source: str = "structured"
    team: str | None = None
    opponent: str | None = None
    player: str | None = None
    zone: str = "unknown"
    ball: _XyModel | None = None
    action: str | None = None
    evidence: list[str] = Field(default_factory=list)
    recommended_inputs: list[str] = Field(default_factory=list)


class SoccerReceiver:
    """Normalizes structured soccer JSON into typed revisions and bus events."""

    def __init__(self, buses: MatrixBuses, profile: SoccerProfile) -> None:
        self.buses = buses
        self.profile = profile
        self.last_heartbeat: SoccerHeartbeat | None = None
        self.last_revision: SoccerEventRevision | None = None
        self.last_sequence: int = -1
        self.match_id: str | None = None
        self.rejected: int = 0

    def ingest(self, payload: dict) -> SoccerHeartbeat | SoccerEventRevision | None:
        kind = str(payload.get("kind") or "").strip().lower()
        if kind == SoccerObservationKind.HEARTBEAT.value:
            return self._ingest_heartbeat(payload)
        if kind == SoccerObservationKind.EVENT.value:
            return self._ingest_event(payload)
        self.rejected += 1
        logger.debug("soccer receiver rejected unknown kind=%s", kind)
        return None

    def is_fresh(self, now: float | None = None) -> bool:
        stamp = self._last_structured_at()
        if stamp is None:
            return False
        age = (now if now is not None else now_ms()) - stamp
        return age <= self.profile.sources.structured_freshness_ms

    def is_stale(self, now: float | None = None) -> bool:
        stamp = self._last_structured_at()
        if stamp is None:
            return True
        age = (now if now is not None else now_ms()) - stamp
        return age > self.profile.sources.structured_stale_ms

    def _last_structured_at(self) -> float | None:
        stamps = []
        if self.last_heartbeat is not None:
            stamps.append(self.last_heartbeat.received_at)
        if self.last_revision is not None:
            stamps.append(self.last_revision.received_at)
        return max(stamps) if stamps else None

    def _ingest_heartbeat(self, payload: dict) -> SoccerHeartbeat | None:
        try:
            model = HeartbeatPayload.model_validate(payload)
        except ValidationError as error:
            self.rejected += 1
            logger.debug("soccer heartbeat invalid: %s", error)
            return None
        if not self._accept_timing(model.timestamp_ms, model.sequence):
            return None
        beat = SoccerHeartbeat(
            match_id=model.match_id,
            timestamp_ms=model.timestamp_ms,
            sequence=model.sequence,
            period=model.period,
            clock_mmss=model.clock_mmss,
            home_score=model.home_score,
            away_score=model.away_score,
            source=model.source,
            confidence=model.confidence,
        )
        self.last_heartbeat = beat
        self.last_sequence = model.sequence
        self.match_id = model.match_id
        self.buses.game_state.publish(
            GameStateEvent(
                type="soccer_heartbeat",
                data={
                    "match_id": beat.match_id,
                    "sequence": beat.sequence,
                    "period": beat.period,
                    "clock_mmss": beat.clock_mmss,
                },
            )
        )
        return beat

    def _ingest_event(self, payload: dict) -> SoccerEventRevision | None:
        try:
            model = EventPayload.model_validate(payload)
        except ValidationError as error:
            self.rejected += 1
            logger.debug("soccer event invalid: %s", error)
            return None
        if not self._accept_timing(model.timestamp_ms, model.sequence):
            return None
        revision = SoccerEventRevision(
            kind=model.kind,
            revision=model.revision,
            event_id=model.event_id,
            match_id=model.match_id,
            event_type=model.event_type,
            timestamp_ms=model.timestamp_ms,
            sequence=model.sequence,
            confidence=model.confidence,
            correlation_id=model.correlation_id,
            phase=model.phase,
            source=model.source,
            team=model.team,
            opponent=model.opponent,
            player=model.player,
            zone=model.zone,
            ball=PitchPoint(x=model.ball.x, y=model.ball.y) if model.ball else None,
            action=model.action,
            evidence=list(model.evidence),
            recommended_inputs=list(model.recommended_inputs),
        )
        self.last_revision = revision
        self.last_sequence = model.sequence
        self.match_id = model.match_id
        self.buses.game_state.publish(
            GameStateEvent(
                type=f"soccer_{revision.event_type}",
                data={
                    "revision": revision.revision,
                    "event_id": revision.event_id,
                    "correlation_id": revision.correlation(),
                    "phase": revision.phase,
                    "confidence": revision.confidence,
                    "zone": revision.zone,
                },
            )
        )
        return revision

    def heartbeat_overdue(self, now: float | None = None) -> bool:
        """True when the feed has missed at least two expected heartbeats."""
        if self.last_heartbeat is None:
            return True
        age = (now if now is not None else now_ms()) - self.last_heartbeat.received_at
        return age > self.profile.sources.structured_heartbeat_ms * 2

    def _accept_timing(self, timestamp_ms: float, sequence: int) -> bool:
        now = now_ms()
        if timestamp_ms - now > self.profile.sources.max_future_skew_ms:
            self.rejected += 1
            logger.debug("soccer payload rejected future timestamp")
            return False
        if now - timestamp_ms > self.profile.sources.structured_stale_ms:
            # A payload this old must not refresh source freshness or mutate
            # event state; drop it instead of pretending the feed is alive.
            self.rejected += 1
            logger.debug("soccer payload rejected stale timestamp")
            return False
        if sequence < self.last_sequence:
            self.rejected += 1
            logger.debug(
                "soccer payload rejected out-of-order sequence %s < %s",
                sequence,
                self.last_sequence,
            )
            return False
        return True
