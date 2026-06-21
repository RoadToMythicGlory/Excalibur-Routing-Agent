from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from obs_auto_director.config import DirectorConfig, GameProfile
from obs_auto_director.detection.rules import Detection

logger = logging.getLogger(__name__)

# Event-name stems whose scoring weight lives under a different key.
_WEIGHT_ALIASES = {"critical_survival": "low_health"}
_PLAYER_PREFIX = re.compile(r"^player_\d+_")


@dataclass(frozen=True)
class SceneDecision:
    scene: str
    reason: str
    confidence: float
    event_name: str | None = None
    category: str | None = None
    priority: int = 0


class DirectorEngine:
    def __init__(self, config: DirectorConfig, profile: GameProfile | None = None) -> None:
        self.config = config
        self.profile = profile
        self.current_scene = config.default_scene
        self.current_event_name: str | None = None
        self.current_category: str | None = None
        self.current_priority = 0
        self.current_score = 0.0
        self.last_switch_at = 0.0
        self.last_detection_at = time.time()
        self.hold_until = 0.0
        self.stable_counts: dict[str, int] = {}
        self.recent_events: dict[str, Detection] = {}
        self.unhealthy_sources: set[str] = set()

        # Behaviour derived from the game profile's scoring + cut_rules.
        self.scoring_enabled = bool(
            profile and profile.scoring.enabled and profile.scoring.weights
        )
        self.score_margin: float | None = None
        self.interrupt_priority: int | None = None
        self.health_gate = False
        if profile is not None:
            for cut_rule in profile.cut_rules:
                if cut_rule.type == "score_margin":
                    self.score_margin = cut_rule.margin
                elif cut_rule.type == "interrupt_priority":
                    self.interrupt_priority = cut_rule.min_priority
                elif cut_rule.type == "source_health_gate":
                    self.health_gate = bool(cut_rule.require_healthy)

    def set_source_health(self, source: str, healthy: bool) -> None:
        """Record feed health so the source_health_gate cut rule can apply."""
        if healthy:
            self.unhealthy_sources.discard(source)
        else:
            self.unhealthy_sources.add(source)

    def score_detection(self, detection: Detection, now: float) -> float:
        """Weighted, time-decayed score for a detection.

        Falls back to the raw priority when no scoring profile is configured so
        ranking still works exactly as before.
        """
        if not self.scoring_enabled or self.profile is None:
            return float(detection.priority)

        weights = self.profile.scoring.weights
        stem = _PLAYER_PREFIX.sub("", detection.event_name)
        stem = _WEIGHT_ALIASES.get(stem, stem)
        weight = (
            weights.get(detection.event_name)
            or weights.get(stem)
            or weights.get(detection.category)
            or 0.0
        )
        if weight <= 0:
            # Keep unweighted events comparable but below any weighted event.
            return detection.priority * 0.001

        decay = self.profile.scoring.decay
        decay_seconds = {
            "kill": decay.kill_sec,
            "objective": decay.objective_sec,
            "utility": decay.utility_sec,
            "combat": decay.combat_sec,
            "clutch": decay.clutch_sec,
        }.get(detection.category, decay.combat_sec)
        age = max(0.0, now - detection.detected_at)
        factor = max(0.0, 1.0 - age / max(decay_seconds, 0.001))
        return weight * factor

    def decide(self, detections: list[Detection]) -> SceneDecision | None:
        now = time.time()
        candidates = [
            detection
            for detection in detections
            if detection.confidence >= self.config.min_confidence
            and not self._gated_by_health(detection)
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
            best = self._best_detection(stable_candidates, now)
            self._log_selection(best, stable_candidates)
            return self._decision_for_detection(best, now)

        if self.recent_events:
            best_recent = self._best_detection(list(self.recent_events.values()), now)
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
            self.current_score = 0.0
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
                self.current_score = self.score_detection(detection, now)
            return None

        if self._blocked_by_score_margin(detection, now):
            logger.debug(
                "score margin holds scene=%s; candidate=%s score gap < %.1f",
                self.current_scene,
                detection.event_name,
                self.score_margin or 0.0,
            )
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

        decision = self._switch(
            scene=detection.scene,
            reason=(
                f"cs2_event:{detection.event_name}:category:{detection.category}:"
                f"priority:{detection.priority}:source:{detection.source}:"
                f"target:{detection.target_source or detection.source}"
            ),
            confidence=detection.confidence,
            event_name=detection.event_name,
            category=detection.category,
            priority=detection.priority,
            now=now,
            hold_sec=detection.hold_sec,
            bypass_cooldown=bypass_cooldown,
        )
        if decision is not None:
            self.current_score = self.score_detection(detection, now)
        return decision

    def _can_preempt(self, detection: Detection) -> bool:
        if detection.interrupt:
            return True
        if self.interrupt_priority is not None and detection.priority >= self.interrupt_priority:
            return True
        return detection.priority >= self.current_priority + self.config.preempt_priority_gap

    def _gated_by_health(self, detection: Detection) -> bool:
        if not self.health_gate or not self.unhealthy_sources:
            return False
        target = detection.target_source or detection.source
        return target in self.unhealthy_sources

    def _blocked_by_score_margin(self, detection: Detection, now: float) -> bool:
        if not self.scoring_enabled or self.score_margin is None:
            return False
        if detection.interrupt or self._can_preempt(detection):
            return False
        candidate_score = self.score_detection(detection, now)
        return (candidate_score - self.current_score) < self.score_margin

    def _best_detection(self, detections: list[Detection], now: float | None = None) -> Detection:
        moment = time.time() if now is None else now
        return max(
            detections,
            key=lambda detection: (
                self.score_detection(detection, moment),
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
