from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
import logging

import cv2
import numpy as np

from obs_auto_director.capture.manager import Frame, crop_region
from obs_auto_director.config import (
    EventConfig,
    GameProfile,
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


class RuleEvaluator:
    def __init__(self, profile: GameProfile, config_dir: Path) -> None:
        self.profile = profile
        self.config_dir = config_dir
        self.previous_gray_by_key: dict[str, np.ndarray] = {}
        self.template_cache: dict[str, np.ndarray | None] = {}
        self.missing_template_warnings: set[str] = set()

    def evaluate(self, frames: list[Frame]) -> list[Detection]:
        frames_by_source = {frame.source: frame for frame in frames}
        detections: list[Detection] = []

        for event in self.profile.events:
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
                detections.append(
                    Detection(
                        event_name=event.name,
                        source=source,
                        confidence=confidence,
                        priority=event.priority,
                        scene=event.scene,
                        category=event.category,
                        description=event.description,
                        hold_sec=event.hold_sec,
                        stable_frames=event.stable_frames,
                        interrupt=event.interrupt,
                        detected_at=time.time(),
                    )
                )

        return detections

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
