from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from obs_auto_director.config import DirectorConfig
from obs_auto_director.detection.rules import Detection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SceneDecision:
    scene: str
    reason: str
    confidence: float
    event_name: str | None = None
    category: str | None = None
    priority: int = 0


class DirectorEngine:
    def __init__(self, config: DirectorConfig) -> None:
        self.config = config
        self.current_scene = config.default_scene
        self.current_event_name: str | None = None
        self.current_category: str | None = None
        self.current_priority = 0
        self.last_switch_at = 0.0
        self.last_detection_at = time.time()
        self.hold_until = 0.0
        self.stable_counts: dict[str, int] = {}
        self.recent_events: dict[str, Detection] = {}

    def decide(self, detections: list[Detection]) -> SceneDecision | None:
        now = time.time()
        candidates = [
            detection
            for detection in detections
            if detection.confidence >= self.config.min_confidence
        ]

        if candidates:
            self.last_detection_at = now

        active_names = {candidate.event_name for candidate in candidates}
        for event_name in list(self.stable_counts):
            if event_name not in active_names:
                self.stable_counts.pop(event_name)

        for candidate in candidates:
            self.stable_counts[candidate.event_name] = (
                self.stable_counts.get(candidate.event_name, 0) + 1
            )

        stable_candidates = [
            candidate
            for candidate in candidates
            if self.stable_counts.get(candidate.event_name, 0) >= candidate.stable_frames
        ]
        for candidate in stable_candidates:
            self.recent_events[candidate.event_name] = candidate
        self._prune_recent_events(now)

        if stable_candidates:
            best = self._best_detection(stable_candidates)
            self._log_selection(best, stable_candidates)
            return self._decision_for_detection(best, now)

        if self.recent_events:
            best_recent = self._best_detection(list(self.recent_events.values()))
            self._log_selection(best_recent, list(self.recent_events.values()), recent=True)
            decision = self._decision_for_detection(best_recent, now, recent=True)
            if decision is not None:
                self.recent_events.pop(best_recent.event_name, None)
            return decision

        if (
            self.current_scene != self.config.default_scene
            and now > self.hold_until
            and now - self.last_detection_at >= self.config.fallback_after_sec
        ):
            return self._switch(
                scene=self.config.default_scene,
                reason="fallback_after_no_active_cs2_event",
                confidence=1.0,
                event_name=None,
                category=None,
                priority=0,
                now=now,
                hold_sec=0.0,
            )

        return None

    def _decision_for_detection(
        self, detection: Detection, now: float, recent: bool = False
    ) -> SceneDecision | None:
        if detection.scene == self.current_scene:
            if not recent:
                self.hold_until = max(self.hold_until, now + detection.hold_sec)
            return None

        bypass_cooldown = now < self.hold_until and self._can_preempt(detection)
        if now < self.hold_until and not bypass_cooldown:
            logger.debug(
                "holding scene=%s event=%s priority=%s; ignored event=%s "
                "category=%s priority=%s confidence=%.3f until hold expires",
                self.current_scene,
                self.current_event_name,
                self.current_priority,
                detection.event_name,
                detection.category,
                detection.priority,
                detection.confidence,
            )
            return None

        return self._switch(
            scene=detection.scene,
            reason=(
                f"cs2_event:{detection.event_name}:category:{detection.category}:"
                f"priority:{detection.priority}:source:{detection.source}"
            ),
            confidence=detection.confidence,
            event_name=detection.event_name,
            category=detection.category,
            priority=detection.priority,
            now=now,
            hold_sec=detection.hold_sec,
            bypass_cooldown=bypass_cooldown,
        )

    def _can_preempt(self, detection: Detection) -> bool:
        if detection.interrupt:
            return True
        return detection.priority >= self.current_priority + self.config.preempt_priority_gap

    def _best_detection(self, detections: list[Detection]) -> Detection:
        return max(
            detections,
            key=lambda detection: (
                detection.priority,
                detection.confidence,
                detection.detected_at,
            ),
        )

    def _prune_recent_events(self, now: float) -> None:
        expired = [
            event_name
            for event_name, detection in self.recent_events.items()
            if now - detection.detected_at > self.config.recent_event_ttl_sec
        ]
        for event_name in expired:
            self.recent_events.pop(event_name, None)

    def _log_selection(
        self, best: Detection, detections: list[Detection], recent: bool = False
    ) -> None:
        ignored = [
            detection
            for detection in detections
            if detection.event_name != best.event_name
        ]
        logger.debug(
            "selected %s event=%s category=%s priority=%s confidence=%.3f scene=%s",
            "recent" if recent else "live",
            best.event_name,
            best.category,
            best.priority,
            best.confidence,
            best.scene,
        )
        for detection in ignored:
            logger.debug(
                "ignored lower-ranked event=%s category=%s priority=%s "
                "confidence=%.3f scene=%s; winner=%s",
                detection.event_name,
                detection.category,
                detection.priority,
                detection.confidence,
                detection.scene,
                best.event_name,
            )

    def _switch(
        self,
        scene: str,
        reason: str,
        confidence: float,
        event_name: str | None,
        category: str | None,
        priority: int,
        now: float,
        hold_sec: float,
        bypass_cooldown: bool = False,
    ) -> SceneDecision | None:
        if not bypass_cooldown and now - self.last_switch_at < self.config.switch_cooldown_sec:
            return None

        self.current_scene = scene
        self.current_event_name = event_name
        self.current_category = category
        self.current_priority = priority
        self.last_switch_at = now
        self.hold_until = now + hold_sec
        return SceneDecision(
            scene=scene,
            reason=reason,
            confidence=confidence,
            event_name=event_name,
            category=category,
            priority=priority,
        )
