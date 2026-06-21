from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
import logging

import cv2
import numpy as np

from obs_auto_director.capture.manager import Frame, crop_region
from obs_auto_director.config import (
    AliveCountRuleConfig,
    EventConfig,
    GameProfile,
    KillFeedChangeRuleConfig,
    KillFeedMultiRuleConfig,
    MotionRuleConfig,
    RegionBrightnessRuleConfig,
    RegionColorRuleConfig,
    RuleConfig,
    TemplateRuleConfig,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Detection:
    event_name: str
    source: str
    confidence: float
    priority: int
    scene: str
    category: str
    description: str | None
    hold_sec: float
    stable_frames: int
    interrupt: bool
    detected_at: float
    # Feed the event resolved to (for "dynamic" routing); falls back to ``source``.
    target_source: str | None = None


class RuleEvaluator:
    def __init__(self, profile: GameProfile, config_dir: Path) -> None:
        self.profile = profile
        self.config_dir = config_dir
        self.previous_gray_by_key: dict[str, np.ndarray] = {}
        self.template_cache: dict[str, np.ndarray | None] = {}
        self.missing_template_warnings: set[str] = set()
        # Rolling kill-feed entry timestamps keyed by source:region.
        self.kill_feed_history: dict[str, list[float]] = {}
        # Per-frame cache so a kill-feed region is diffed once even when several
        # rules (e.g. double + triple kill) read the same region.
        self._frame_kill_entries: dict[str, int] = {}
        # Optional external alive-count snapshot (CT, T) for clutch rules.
        self._alive_counts: tuple[int, int] | None = None
        self._alive_count_warned = False

    def set_alive_counts(self, counts: tuple[int, int] | None) -> None:
        """Inject the current per-team alive counts from a game-state source.

        Vision alone cannot read alive counts from the observer feed, so clutch
        rules stay dormant until this snapshot is provided (e.g. from GSI).
        """
        self._alive_counts = counts

    def evaluate(self, frames: list[Frame]) -> list[Detection]:
        frames_by_source = {frame.source: frame for frame in frames}
        self._frame_kill_entries = {}
        detections: list[Detection] = []

        for event in self.profile.all_events:
            confidences: list[tuple[str, float]] = []
            for rule in event.rules:
                frame = frames_by_source.get(rule.source)
                if frame is None:
                    continue

                confidence = self._evaluate_rule(event.name, rule, frame)
                if confidence > 0:
                    confidences.append((rule.source, confidence))

            event_confidence = self._event_confidence(event, confidences)
            if event_confidence is not None:
                source, confidence = event_confidence
                scene, target_source = self._resolve_routing(event, source)
                detections.append(
                    Detection(
                        event_name=event.name,
                        source=source,
                        confidence=confidence,
                        priority=event.priority,
                        scene=scene,
                        category=event.category,
                        description=event.description,
                        hold_sec=event.hold_sec,
                        stable_frames=event.stable_frames,
                        interrupt=event.interrupt,
                        detected_at=time.time(),
                        target_source=target_source,
                    )
                )

        return detections

    def _resolve_routing(
        self, event: EventConfig, winning_source: str
    ) -> tuple[str, str | None]:
        """Resolve a "dynamic" scene/target to the feed that actually fired."""
        is_dynamic = event.scene == "dynamic" or event.target_source == "dynamic"
        if not is_dynamic:
            return event.scene, event.target_source or event.source or winning_source

        resolved = self.profile.scene_for_source(winning_source)
        target = winning_source
        if resolved is None and event.source:
            resolved = self.profile.scene_for_source(event.source)
            target = event.source
        if resolved is None:
            resolved = self.profile.defaults.fallback_scene or event.scene
            target = event.source or winning_source
        return resolved, target

    def _event_confidence(
        self, event: EventConfig, confidences: list[tuple[str, float]]
    ) -> tuple[str, float] | None:
        if not confidences:
            return None

        if event.rule_mode == "all" and len(confidences) != len(event.rules):
            return None

        source, strongest = max(confidences, key=lambda item: item[1])
        if event.rule_mode == "all":
            weakest = min(confidence for _, confidence in confidences)
            return source, weakest

        return source, strongest

    def _evaluate_rule(self, event_name: str, rule: RuleConfig, frame: Frame) -> float:
        if isinstance(rule, AliveCountRuleConfig):
            return self._alive_count_confidence(rule)

        image = frame.image_bgr
        if rule.region is not None:
            image = crop_region(image, rule.region)

        if isinstance(rule, TemplateRuleConfig):
            return self._template_confidence(rule, image)
        if isinstance(rule, RegionBrightnessRuleConfig):
            return self._brightness_confidence(rule, image)
        if isinstance(rule, RegionColorRuleConfig):
            return self._color_confidence(rule, image)
        if isinstance(rule, MotionRuleConfig):
            return self._motion_confidence(event_name, rule, frame.source, image)
        if isinstance(rule, KillFeedChangeRuleConfig):
            entries = self._kill_feed_entries(
                rule.source, rule.region, image, rule.pixel_threshold, rule.change_ratio_per_entry
            )
            if entries < rule.min_new_entries:
                return 0.0
            return min(1.0, entries / max(rule.min_new_entries, 1))
        if isinstance(rule, KillFeedMultiRuleConfig):
            return self._kill_feed_multi_confidence(rule, image)

        return 0.0

    def _template_confidence(self, rule: TemplateRuleConfig, image_bgr: np.ndarray) -> float:
        template = self._load_template(rule.template_path)
        if template is None:
            return 0.0
        if image_bgr.shape[0] < template.shape[0] or image_bgr.shape[1] < template.shape[1]:
            return 0.0

        result = cv2.matchTemplate(image_bgr, template, cv2.TM_CCOEFF_NORMED)
        confidence = float(result.max())
        return confidence if confidence >= rule.threshold else 0.0

    def _brightness_confidence(
        self, rule: RegionBrightnessRuleConfig, image_bgr: np.ndarray
    ) -> float:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        mean_brightness = float(gray.mean())
        if rule.operator == "above":
            if mean_brightness < rule.threshold:
                return 0.0
            return min(1.0, mean_brightness / max(rule.threshold, 1.0))

        if mean_brightness > rule.threshold:
            return 0.0
        return min(1.0, (rule.threshold - mean_brightness) / max(rule.threshold, 1.0))

    def _color_confidence(self, rule: RegionColorRuleConfig, image_bgr: np.ndarray) -> float:
        lower = np.array(rule.lower_bgr, dtype=np.uint8)
        upper = np.array(rule.upper_bgr, dtype=np.uint8)
        mask = cv2.inRange(image_bgr, lower, upper)
        ratio = float(np.count_nonzero(mask)) / float(mask.size)
        if ratio < rule.min_ratio:
            return 0.0
        return min(1.0, ratio / max(rule.min_ratio, 0.001))

    def _motion_confidence(
        self,
        event_name: str,
        rule: MotionRuleConfig,
        source: str,
        image_bgr: np.ndarray,
    ) -> float:
        key = f"{event_name}:{source}:{rule.region}"
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        previous = self.previous_gray_by_key.get(key)
        self.previous_gray_by_key[key] = gray

        if previous is None:
            return 0.0

        diff = cv2.absdiff(previous, gray)
        _, thresholded = cv2.threshold(diff, rule.threshold, 255, cv2.THRESH_BINARY)
        ratio = float(np.count_nonzero(thresholded)) / float(thresholded.size)
        if ratio < rule.min_ratio:
            return 0.0
        return min(1.0, ratio / max(rule.min_ratio, 0.001))

    def _kill_feed_entries(
        self,
        source: str,
        region,
        image_bgr: np.ndarray,
        pixel_threshold: float,
        change_ratio_per_entry: float,
    ) -> int:
        """Estimate how many new kill-feed rows appeared this frame.

        The kill feed grows by discrete rows, so a burst of changed pixels in
        the feed region maps to an entry count. Diffed once per frame per region
        and the result is cached so multi-kill rules reuse it without consuming
        the change.
        """
        key = f"{source}:{tuple(region)}"
        if key in self._frame_kill_entries:
            return self._frame_kill_entries[key]

        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        previous = self.previous_gray_by_key.get(f"killfeed:{key}")
        self.previous_gray_by_key[f"killfeed:{key}"] = gray

        entries = 0
        if previous is not None and previous.shape == gray.shape:
            diff = cv2.absdiff(previous, gray)
            _, thresholded = cv2.threshold(diff, pixel_threshold, 255, cv2.THRESH_BINARY)
            ratio = float(np.count_nonzero(thresholded)) / float(thresholded.size)
            per_entry = max(change_ratio_per_entry, 1e-6)
            entries = int(ratio / per_entry)

        self._frame_kill_entries[key] = entries
        if entries > 0:
            history = self.kill_feed_history.setdefault(key, [])
            now = time.time()
            history.extend([now] * entries)
        return entries

    def _kill_feed_multi_confidence(
        self, rule: KillFeedMultiRuleConfig, image_bgr: np.ndarray
    ) -> float:
        # Ensure this frame's entries are counted into the rolling history.
        self._kill_feed_entries(
            rule.source,
            rule.region,
            image_bgr,
            rule.pixel_threshold,
            rule.change_ratio_per_entry,
        )
        key = f"{rule.source}:{tuple(rule.region)}"
        now = time.time()
        history = self.kill_feed_history.get(key, [])
        recent = [stamp for stamp in history if now - stamp <= rule.window_sec]
        self.kill_feed_history[key] = recent
        if len(recent) < rule.same_player_kills:
            return 0.0
        return min(1.0, len(recent) / max(rule.same_player_kills, 1))

    def _alive_count_confidence(self, rule: AliveCountRuleConfig) -> float:
        if self._alive_counts is None:
            if not self._alive_count_warned:
                logger.info(
                    "alive_count rules are dormant: no game-state alive counts "
                    "wired in (call RuleEvaluator.set_alive_counts)."
                )
                self._alive_count_warned = True
            return 0.0

        if rule.team_a_alive is not None and rule.team_b_alive is not None:
            targets = sorted((rule.team_a_alive, rule.team_b_alive))
        elif rule.one_side_alive is not None and rule.other_side_alive is not None:
            targets = sorted((rule.one_side_alive, rule.other_side_alive))
        else:
            return 0.0

        current = sorted(self._alive_counts)
        return 1.0 if current == targets else 0.0

    def _load_template(self, template_path: str) -> np.ndarray | None:
        path = (self.config_dir / template_path).resolve()
        cache_key = str(path)
        if cache_key not in self.template_cache:
            template = cv2.imread(cache_key, cv2.IMREAD_COLOR)
            if template is None:
                if cache_key not in self.missing_template_warnings:
                    logger.warning("Template image is missing or unreadable: %s", path)
                    self.missing_template_warnings.add(cache_key)
                self.template_cache[cache_key] = None
                return None
            self.template_cache[cache_key] = template
        return self.template_cache[cache_key]
