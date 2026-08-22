from __future__ import annotations

import logging

from excalibur.matrix.buses import MatrixBuses
from excalibur.matrix.clock import now_ms
from excalibur.matrix.domain import BusName, RoutingIntent
from excalibur.matrix.matrix_core import MatrixCore
from excalibur.matrix.soccer.camera_map import SoccerCameraMap
from excalibur.matrix.soccer.config import SoccerProfile
from excalibur.matrix.soccer.domain import (
    CameraAssignment,
    CameraRole,
    DirectorMode,
    EventLifecycle,
    SoccerEventState,
    SoccerMatchView,
    SoccerPhase,
    SourceAuthority,
)
from excalibur.matrix.soccer.fusion import SoccerFusion

logger = logging.getLogger(__name__)

ROLE_WEIGHT = {
    "preferred": 40.0,
    "fallback": 18.0,
    "main": 12.0,
    "unknown": -8.0,
}


class SoccerDirector:
    """Stateful soccer shot planner. Publishes intents; never writes routes."""

    def __init__(
        self,
        buses: MatrixBuses,
        matrix: MatrixCore,
        fusion: SoccerFusion,
        cameras: SoccerCameraMap,
        profile: SoccerProfile,
        name: str = "soccer-director",
    ) -> None:
        self.buses = buses
        self.matrix = matrix
        self.fusion = fusion
        self.cameras = cameras
        self.profile = profile
        self.name = name
        self.mode = DirectorMode.AUTOMATIC
        self.last_switch_at: float = 0.0
        self.last_dwell_input: str | None = None
        self.last_score: float = 0.0
        self.event_ended_at: float | None = None
        self._last_event_id: str | None = None

    @property
    def enabled(self) -> bool:
        return self.mode == DirectorMode.AUTOMATIC

    def set_mode(self, mode: DirectorMode | str) -> DirectorMode:
        self.mode = DirectorMode(mode)
        return self.mode

    def tick(self, now: float | None = None) -> RoutingIntent | None:
        now = now if now is not None else now_ms()
        view = self.fusion.tick(now)
        self._sync_cameras(view)
        if self.mode != DirectorMode.AUTOMATIC:
            return None
        event = view.event
        current = self.matrix.current_input_for(self.profile.program_output)
        if current not in (None, self.profile.main_input):
            slot = self.matrix.inputs.get(current)
            if slot is None or not slot.has_signal:
                return self._return_main(now, "signal_lost")
        if view.authority == SourceAuthority.NONE.value:
            if event is None or event.lifecycle != EventLifecycle.ACTIVE.value:
                return self._return_main(
                    now, view.fallback_reason or "no_trustworthy_source"
                )
            # Mid-event blackout: hold the current shot rather than keep
            # cutting on data nobody is corroborating. Fusion winds the event
            # down (source_lost) if the sources stay dark.
            return None
        if event is None or event.lifecycle == EventLifecycle.ENDED.value:
            return self._maybe_return_main(view, now)
        if event.lifecycle != EventLifecycle.ACTIVE.value:
            return None
        return self._cut_for_event(event, view, now)

    def _sync_cameras(self, view: SoccerMatchView) -> None:
        self.cameras.pin_main()
        for assignment in view.cameras.values():
            self.cameras.update(assignment)

    def _maybe_return_main(
        self, view: SoccerMatchView, now: float
    ) -> RoutingIntent | None:
        current = self.matrix.current_input_for(self.profile.program_output)
        if current in (None, self.profile.main_input):
            self.event_ended_at = None
            return None
        if self.event_ended_at is None:
            self.event_ended_at = now
        if now - self.event_ended_at < self.profile.return_to_main.after_end_ms:
            return None
        reason = view.fallback_reason or "event_over"
        return self._return_main(now, reason)

    def _return_main(self, now: float, reason: str) -> RoutingIntent | None:
        current = self.matrix.current_input_for(self.profile.program_output)
        if current == self.profile.main_input:
            return None
        if not self.matrix.input_available(self.profile.main_input):
            return None
        return self._publish(
            self.profile.main_input,
            f"soccer_return_main:{reason}",
            self.profile.return_to_main.priority,
            self.profile.return_to_main.confidence,
            hold_ms=0.0,
            interrupt=True,
            now=now,
        )

    def _cut_for_event(
        self, event: SoccerEventState, view: SoccerMatchView, now: float
    ) -> RoutingIntent | None:
        if event.event_id != self._last_event_id:
            self._last_event_id = event.event_id
            self.event_ended_at = None
        ranked = self.rank_inputs(event, view, now)
        if not ranked:
            return self._return_main(now, "no_eligible_angle")
        candidate, score, assignment = ranked[0]
        current = self.matrix.current_input_for(self.profile.program_output)
        if current == candidate:
            self.last_dwell_input = candidate
            self.last_score = score
            return None
        if current is not None:
            if now - self.last_switch_at < self.profile.gating.cut_cooldown_ms:
                return None
            if (
                self.last_dwell_input == current
                and now - self.last_switch_at < self.profile.gating.min_dwell_ms
            ):
                return None
            if score - self.last_score < self.profile.gating.score_margin:
                return None
        plan = self.profile.event_plan(event.event_type)
        return self._publish(
            candidate,
            (
                f"soccer_event:{event.event_type}:phase:{event.phase}"
                f":role:{assignment.role}:score:{score:.1f}"
            ),
            plan.priority,
            event.confidence,
            hold_ms=plan.hold_ms,
            interrupt=plan.interrupt,
            now=now,
            score=score,
        )

    def rank_inputs(
        self, event: SoccerEventState, view: SoccerMatchView, now: float
    ) -> list[tuple[str, float, CameraAssignment]]:
        plan = self.profile.event_plan(event.event_type)
        phase = (
            plan.phases.get(event.phase)
            or plan.phases.get(SoccerPhase.SETUP.value)
            or next(iter(plan.phases.values()))
        )
        ranked: list[tuple[str, float, CameraAssignment]] = []
        for input_id, slot in self.matrix.inputs.items():
            if not slot.has_signal:
                continue
            assignment = self.cameras.role_for(input_id)
            if assignment.input_id in view.cameras:
                live = view.cameras[assignment.input_id]
                assignment.visible_ball = live.visible_ball
                assignment.sharpness = live.sharpness
                assignment.occlusion = live.occlusion
                assignment.coverage = live.coverage
                assignment.latency_ms = live.latency_ms
            if (
                assignment.role != CameraRole.MAIN_WIDE.value
                and assignment.role != CameraRole.UNKNOWN.value
                and assignment.confidence < self.profile.gating.min_role_confidence
            ):
                continue
            score = self.score_assignment(assignment, event, phase.preferred_roles, phase.fallback_roles)
            assignment.score = score
            if score <= 0 and assignment.role != CameraRole.MAIN_WIDE.value:
                continue
            ranked.append((input_id, score, assignment))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    def score_assignment(
        self,
        assignment: CameraAssignment,
        event: SoccerEventState,
        preferred: list[str],
        fallback: list[str],
    ) -> float:
        score = 0.0
        if assignment.role in preferred:
            score += ROLE_WEIGHT["preferred"] + max(
                0, 8 - preferred.index(assignment.role)
            )
        elif assignment.role in fallback:
            score += ROLE_WEIGHT["fallback"]
        elif assignment.role == CameraRole.MAIN_WIDE.value:
            score += ROLE_WEIGHT["main"]
        elif assignment.role == CameraRole.UNKNOWN.value:
            score += ROLE_WEIGHT["unknown"]
        score += 18.0 * assignment.confidence
        score += 10.0 if assignment.visible_ball else 0.0
        score += 8.0 * assignment.coverage
        score += 6.0 * assignment.sharpness
        score -= 12.0 * assignment.occlusion
        score -= min(8.0, assignment.latency_ms / 80.0)
        if event.recommended_inputs and assignment.input_id in event.recommended_inputs:
            score += 15.0
        if assignment.input_id == self.last_dwell_input:
            score += 4.0
        return score

    def _publish(
        self,
        candidate: str,
        reason: str,
        priority: int,
        confidence: float,
        hold_ms: float,
        interrupt: bool,
        now: float,
        score: float | None = None,
    ) -> RoutingIntent:
        intent = RoutingIntent(
            producer=self.name,
            bus=BusName.DECISION,
            target_output=self.profile.program_output,
            candidate_input=candidate,
            reason=reason,
            priority=priority,
            confidence=confidence,
            ttl_ms=2500.0,
            interrupt=interrupt,
            hold_ms=hold_ms,
        )
        self.buses.decision.publish(intent)
        self.last_switch_at = now
        self.last_dwell_input = candidate
        if score is not None:
            self.last_score = score
        return intent
