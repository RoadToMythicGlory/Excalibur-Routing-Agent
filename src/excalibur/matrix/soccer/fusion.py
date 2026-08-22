from __future__ import annotations

import logging

from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import now_ms
from excalibur.matrix.domain import MatchIntelligenceEvent
from excalibur.matrix.soccer.config import SoccerProfile
from excalibur.matrix.soccer.domain import (
    FOUL_TRANSITIONS,
    SPECIAL_EVENT_TYPES,
    CameraAssignment,
    EventLifecycle,
    SoccerEventRevision,
    SoccerEventState,
    SoccerEventType,
    SoccerHeartbeat,
    SoccerMatchView,
    SoccerObservationKind,
    SoccerPhase,
    SoccerRevision,
    SourceAuthority,
    VisionObservation,
    new_event_id,
)

logger = logging.getLogger(__name__)


class SoccerFusion:
    """Arbitrates structured feed vs vision and owns the event lifecycle."""

    def __init__(self, buses: MatrixBuses, profile: SoccerProfile) -> None:
        self.buses = buses
        self.profile = profile
        self.match_id: str | None = None
        self.last_heartbeat: SoccerHeartbeat | None = None
        self.last_vision_at: float | None = None
        self.model_version: str | None = None
        self.event: SoccerEventState | None = None
        self.cameras: dict[str, CameraAssignment] = {}
        self.fallback_reason: str | None = None
        self._pending_end_since: float | None = None
        # False while the CV model runs in shadow mode: camera roles and
        # freshness still update, but vision may never become the routing
        # authority. The vision agent sets this from its evaluation-gate check.
        self.vision_authority_allowed: bool = True

    def ingest_structured(
        self, item: SoccerHeartbeat | SoccerEventRevision | None
    ) -> SoccerMatchView:
        if isinstance(item, SoccerHeartbeat):
            self.last_heartbeat = item
            self.match_id = item.match_id
        elif isinstance(item, SoccerEventRevision):
            # The receiver already rejects stale/future timestamps, so an
            # accepted revision is proof the structured feed is alive; gating
            # it on prior freshness would drop the first event of a session.
            self.match_id = item.match_id
            self._apply_structured(item)
        return self.view()

    def ingest_camera_assignment(
        self, assignment: CameraAssignment, created_at: float | None = None
    ) -> None:
        """Record a camera-role assignment and mark the vision source alive.

        Used by the vision agent in both live and shadow mode so operators see
        CV health even while it is barred from routing authority.
        """
        self.last_vision_at = created_at if created_at is not None else now_ms()
        self.cameras[assignment.input_id] = assignment

    def ingest_vision(self, observation: VisionObservation) -> SoccerMatchView:
        self.last_vision_at = observation.created_at
        if observation.kind == SoccerObservationKind.CAMERA_ROLE.value:
            if observation.input_id and observation.role:
                self.ingest_camera_assignment(
                    CameraAssignment(
                        input_id=observation.input_id,
                        role=observation.role,
                        confidence=observation.confidence,
                        source="vision",
                    ),
                    created_at=observation.created_at,
                )
            return self.view()
        if (
            observation.kind == SoccerObservationKind.ORDINARY_PASS.value
            and self.profile.gating.ordinary_pass_not_event
        ):
            return self.view()
        if self._structured_fresh():
            return self.view()
        if not self.vision_authority_allowed:
            # Shadow mode: vision may refresh camera roles above, but must not
            # create or mutate event state.
            return self.view()
        if observation.kind == SoccerObservationKind.OPEN_PLAY.value:
            self._request_end("vision_open_play", observation.created_at)
            return self.view()
        if (
            observation.event_type in SPECIAL_EVENT_TYPES
            and observation.confidence >= self.profile.gating.min_event_confidence
        ):
            self._apply_vision_event(observation)
        return self.view()

    def tick(self, now: float | None = None) -> SoccerMatchView:
        now = now if now is not None else now_ms()
        event = self.event
        if event is None:
            return self.view(now)
        if event.lifecycle == EventLifecycle.CANDIDATE.value:
            wait = now - (event.candidate_since or event.started_at)
            if wait >= self.profile.gating.start_debounce_ms:
                self._activate(event, now)
        if event.lifecycle == EventLifecycle.ACTIVE.value:
            plan = self.profile.events.get(event.event_type)
            max_ms = (
                plan.max_duration_ms
                if plan is not None
                else self.profile.gating.failsafe_max_event_ms
            )
            failsafe = min(max_ms, self.profile.gating.failsafe_max_event_ms)
            if event.age_ms(now) >= failsafe:
                self._request_end("max_duration", now)
            elif (
                not self._structured_fresh(now)
                and not self._vision_fresh(now)
                and now - event.updated_at >= self.profile.sources.structured_stale_ms
            ):
                # Both sources went dark mid-event: wind the event down so the
                # director can return to the main camera instead of holding a
                # cut nobody is corroborating.
                self._request_end("source_lost", now)
        if event.lifecycle == EventLifecycle.RESOLVING.value:
            since = event.resolving_since or now
            if now - since >= self.profile.gating.end_debounce_ms:
                self._end(event, now)
        return self.view(now)

    def view(self, now: float | None = None) -> SoccerMatchView:
        now = now if now is not None else now_ms()
        structured_fresh = self._structured_fresh(now)
        vision_fresh = self._vision_fresh(now)
        if structured_fresh:
            authority = SourceAuthority.STRUCTURED.value
            self.fallback_reason = None
        elif vision_fresh and self.vision_authority_allowed:
            authority = SourceAuthority.VISION.value
            self.fallback_reason = "structured_stale"
        elif vision_fresh:
            authority = SourceAuthority.NONE.value
            self.fallback_reason = "vision_shadow"
        else:
            authority = SourceAuthority.NONE.value
            self.fallback_reason = "no_trustworthy_source"
        event = self.event
        if event is not None and event.lifecycle == EventLifecycle.ENDED.value:
            event = None
        return SoccerMatchView(
            match_id=self.match_id,
            authority=authority,
            heartbeat_fresh=structured_fresh,
            vision_fresh=vision_fresh,
            fallback_reason=self.fallback_reason,
            event=event,
            cameras=dict(self.cameras),
            profile_version=self.profile.version,
            model_version=self.model_version,
            updated_at=now,
        )

    def _structured_fresh(self, now: float | None = None) -> bool:
        now = now if now is not None else now_ms()
        stamps = []
        if self.last_heartbeat is not None:
            stamps.append(self.last_heartbeat.received_at)
        if self.event is not None and self.event.source == SourceAuthority.STRUCTURED.value:
            stamps.append(self.event.updated_at)
        if not stamps:
            return False
        return (now - max(stamps)) <= self.profile.sources.structured_freshness_ms

    def _vision_fresh(self, now: float | None = None) -> bool:
        if self.last_vision_at is None:
            return False
        now = now if now is not None else now_ms()
        return (now - self.last_vision_at) <= self.profile.sources.vision_freshness_ms

    def _apply_structured(self, revision: SoccerEventRevision) -> None:
        if revision.event_type not in SPECIAL_EVENT_TYPES:
            return
        if revision.revision == SoccerRevision.END.value:
            # An end is honored at any confidence; refusing it could pin an
            # event open forever.
            if self.event is not None:
                self._request_end("structured_end", revision.received_at)
            return
        if revision.confidence < self.profile.gating.min_event_confidence:
            logger.debug(
                "soccer structured %s below confidence gate (%.2f < %.2f)",
                revision.event_type,
                revision.confidence,
                self.profile.gating.min_event_confidence,
            )
            return
        current = self.event
        if current and current.lifecycle in (
            EventLifecycle.CANDIDATE.value,
            EventLifecycle.ACTIVE.value,
            EventLifecycle.RESOLVING.value,
        ):
            same = revision.correlation() == current.correlation_id or (
                revision.event_type in FOUL_TRANSITIONS
                and current.event_type == SoccerEventType.FOUL.value
            )
            if same:
                self._update_event(current, revision)
                return
        self._begin_candidate(revision)

    def _apply_vision_event(self, observation: VisionObservation) -> None:
        current = self.event
        if current and current.lifecycle in (
            EventLifecycle.CANDIDATE.value,
            EventLifecycle.ACTIVE.value,
        ):
            if observation.event_type in FOUL_TRANSITIONS and current.event_type == (
                SoccerEventType.FOUL.value
            ):
                current.event_type = observation.event_type
                current.phase = observation.phase or current.phase
                current.confidence = observation.confidence
                current.source = SourceAuthority.VISION.value
                current.updated_at = observation.created_at
                current.evidence.append("vision_transition")
                self._publish(current)
                return
            if observation.event_type == current.event_type:
                current.phase = observation.phase or current.phase
                current.confidence = max(current.confidence, observation.confidence)
                current.updated_at = observation.created_at
                return
        revision = SoccerEventRevision(
            kind="event",
            revision=SoccerRevision.START.value,
            event_id=new_event_id(),
            match_id=self.match_id or "vision",
            event_type=observation.event_type or SoccerEventType.BURST_ATTACK.value,
            timestamp_ms=observation.created_at,
            sequence=0,
            confidence=observation.confidence,
            phase=observation.phase or SoccerPhase.SETUP.value,
            source=SourceAuthority.VISION.value,
            zone=observation.zone or "unknown",
            ball=observation.ball,
            evidence=list(observation.evidence) + ["vision_candidate"],
        )
        self._begin_candidate(revision)

    def _begin_candidate(self, revision: SoccerEventRevision) -> None:
        now = revision.received_at
        state = SoccerEventState(
            event_id=revision.event_id,
            correlation_id=revision.correlation(),
            event_type=revision.event_type,
            lifecycle=EventLifecycle.CANDIDATE.value,
            phase=revision.phase or SoccerPhase.SETUP.value,
            source=revision.source,
            confidence=revision.confidence,
            match_id=revision.match_id,
            team=revision.team,
            player=revision.player,
            zone=revision.zone,
            ball=revision.ball,
            evidence=list(revision.evidence),
            recommended_inputs=list(revision.recommended_inputs),
            started_at=now,
            updated_at=now,
            candidate_since=now,
            last_sequence=revision.sequence,
        )
        self.event = state
        self._pending_end_since = None
        if self.profile.gating.start_debounce_ms <= 0:
            self._activate(state, now)
        logger.info(
            "soccer candidate %s source=%s conf=%.2f",
            state.event_type,
            state.source,
            state.confidence,
        )

    def _update_event(self, current: SoccerEventState, revision: SoccerEventRevision) -> None:
        current.event_type = revision.event_type
        current.phase = revision.phase or current.phase
        current.confidence = revision.confidence
        current.source = revision.source
        current.team = revision.team or current.team
        current.player = revision.player or current.player
        current.zone = revision.zone or current.zone
        current.ball = revision.ball or current.ball
        current.recommended_inputs = list(revision.recommended_inputs) or current.recommended_inputs
        current.evidence = list(dict.fromkeys(current.evidence + revision.evidence))
        current.updated_at = revision.received_at
        current.last_sequence = revision.sequence
        if current.lifecycle == EventLifecycle.RESOLVING.value:
            current.lifecycle = EventLifecycle.ACTIVE.value
            current.resolving_since = None
            self._pending_end_since = None
        if current.lifecycle == EventLifecycle.ACTIVE.value:
            self._publish(current)

    def _activate(self, event: SoccerEventState, now: float) -> None:
        event.lifecycle = EventLifecycle.ACTIVE.value
        event.updated_at = now
        self._publish(event)
        logger.info(
            "soccer active %s phase=%s source=%s",
            event.event_type,
            event.phase,
            event.source,
        )

    def _request_end(self, reason: str, now: float) -> None:
        event = self.event
        if event is None or event.lifecycle == EventLifecycle.ENDED.value:
            return
        if event.lifecycle != EventLifecycle.RESOLVING.value:
            event.lifecycle = EventLifecycle.RESOLVING.value
            event.resolving_since = now
            event.evidence.append(reason)
            event.updated_at = now
            self._pending_end_since = now
            logger.info("soccer resolving %s reason=%s", event.event_type, reason)
        if self.profile.gating.end_debounce_ms <= 0:
            self._end(event, now)

    def _end(self, event: SoccerEventState, now: float) -> None:
        event.lifecycle = EventLifecycle.ENDED.value
        event.updated_at = now
        self._publish(event, ended=True)
        logger.info("soccer ended %s", event.event_type)
        self.event = None
        self._pending_end_since = None

    def _publish(self, event: SoccerEventState, ended: bool = False) -> None:
        plan = self.profile.events.get(event.event_type)
        priority = plan.priority if plan else 50
        ttl = plan.ttl_ms if plan else 2500.0
        story = MatchIntelligenceEvent(
            type=event.event_type,
            round_id=event.match_id,
            priority=priority,
            confidence=event.confidence,
            ttl_ms=ttl,
            recommended_inputs=list(event.recommended_inputs),
            evidence=list(event.evidence),
            lifecycle=EventLifecycle.ENDED.value if ended else event.lifecycle,
            source=event.source,
            correlation_id=event.correlation_id,
            phase=event.phase,
        )
        self.buses.intelligence.publish(story)
