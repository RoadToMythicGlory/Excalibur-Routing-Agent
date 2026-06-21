from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from obs_auto_director.capture.manager import CaptureManager
from obs_auto_director.config import AppConfig, GameProfile, load_app_config, load_game_profile
from obs_auto_director.detection.rules import Detection, RuleEvaluator
from obs_auto_director.director.engine import DirectorEngine
from obs_auto_director.obs.client import ObsSceneRouter

logger = logging.getLogger(__name__)


def collect_expected_scenes(app_config: AppConfig, profile: GameProfile) -> set[str]:
    scenes = {app_config.director.default_scene}
    scenes.update(
        event.scene for event in profile.all_events if event.scene != "dynamic"
    )
    scenes.update(source.scene for source in profile.sources.values())
    if profile.defaults.fallback_scene:
        scenes.add(profile.defaults.fallback_scene)
    return scenes


def apply_profile_defaults(app_config: AppConfig, profile: GameProfile) -> None:
    """Let the game profile's ``defaults`` block steer director timing.

    The app config remains the base; the profile overrides cut cadence, idle
    fallback, and the fallback scene so the YAML the operator tunes is the
    single source of truth for pacing.
    """
    defaults = profile.defaults
    director = app_config.director
    director.switch_cooldown_sec = float(defaults.min_cut_gap_sec)
    director.fallback_after_sec = float(defaults.return_to_observer_after_idle_sec)
    if defaults.fallback_scene:
        director.default_scene = defaults.fallback_scene


class StatusReporter:
    """Throttled heartbeat that makes dry-run tuning readable.

    It reports the current director scene plus the strongest confidence seen
    per event since the last heartbeat, so HUD regions and thresholds can be
    tuned by watching the log instead of guessing.
    """

    def __init__(self, interval_sec: float) -> None:
        self.interval_sec = interval_sec
        self.last_report_at = 0.0
        self.peak_confidence: dict[str, Detection] = {}

    def record(self, detections: list[Detection]) -> None:
        for detection in detections:
            current = self.peak_confidence.get(detection.event_name)
            if current is None or detection.confidence > current.confidence:
                self.peak_confidence[detection.event_name] = detection

    def maybe_report(self, current_scene: str, min_confidence: float) -> None:
        if self.interval_sec <= 0:
            return
        now = time.monotonic()
        if now - self.last_report_at < self.interval_sec:
            return
        self.last_report_at = now

        if self.peak_confidence:
            ranked = sorted(
                self.peak_confidence.values(),
                key=lambda detection: (
                    detection.priority,
                    detection.confidence,
                    detection.detected_at,
                ),
                reverse=True,
            )
            summary = ", ".join(
                f"{detection.event_name}"
                f"[{detection.category}/p{detection.priority}]="
                f"{detection.confidence:.2f}"
                f"{'*' if detection.confidence >= min_confidence else ''}"
                for detection in ranked
            )
        else:
            summary = "no events detected"

        logger.info("[status] live_scene=%s | peak confidences: %s", current_scene, summary)
        self.peak_confidence.clear()


def run(config_path: Path, dry_run_override: bool | None, status_interval: float) -> None:
    app_config = load_app_config(config_path)
    if dry_run_override is not None:
        app_config.obs.dry_run = dry_run_override

    config_dir = config_path.parent
    profile_path = (config_dir / app_config.game_profile).resolve()
    game_profile = load_game_profile(profile_path)
    apply_profile_defaults(app_config, game_profile)

    capture = CaptureManager(app_config.sources)
    evaluator = RuleEvaluator(game_profile, config_dir=config_dir)
    director = DirectorEngine(app_config.director, profile=game_profile)
    obs_router = ObsSceneRouter(app_config.obs)

    expected_scenes = collect_expected_scenes(app_config, game_profile)
    obs_router.connect(expected_scenes=expected_scenes)

    reporter = StatusReporter(status_interval)

    mode = "DRY-RUN" if app_config.obs.dry_run else "LIVE"
    logger.info("OBS Auto Director started for %s in %s mode.", game_profile.game, mode)
    logger.info("Watching sources: %s.", ", ".join(source.name for source in app_config.sources))
    logger.info("Starting live scene: %s.", director.current_scene)

    for frames in capture.frames():
        detections = evaluator.evaluate(frames)
        reporter.record(detections)
        for detection in detections:
            logger.debug(
                "detection event=%s category=%s priority=%s source=%s "
                "confidence=%.3f scene=%s interrupt=%s",
                detection.event_name,
                detection.category,
                detection.priority,
                detection.source,
                detection.confidence,
                detection.scene,
                detection.interrupt,
            )

        decision = director.decide(detections)
        if decision is not None:
            logger.info(
                "director switch -> scene=%s event=%s category=%s priority=%s "
                "confidence=%.3f reason=%s",
                decision.scene,
                decision.event_name,
                decision.category,
                decision.priority,
                decision.confidence,
                decision.reason,
            )
            obs_router.switch_scene(decision.scene, decision.reason)

        reporter.maybe_report(director.current_scene, app_config.director.min_confidence)


def list_monitors() -> None:
    try:
        import mss
    except ImportError as error:  # pragma: no cover - depends on environment
        raise SystemExit(
            "mss is required to list monitors. Install it with: python -m pip install mss"
        ) from error

    with mss.mss() as screen_capture:
        print("Available monitors (index -> geometry):")
        for index, monitor in enumerate(screen_capture.monitors):
            label = "all monitors combined" if index == 0 else f"monitor {index}"
            print(
                f"  [{index}] {label}: "
                f"{monitor['width']}x{monitor['height']} "
                f"at ({monitor['left']}, {monitor['top']})"
            )
    print(
        "\nUse a non-zero index as 'monitor' in your source config. "
        "Index 0 is the virtual screen spanning every monitor."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous OBS director for gaming streams.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/app.example.yaml"),
        help="Path to the app YAML config.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity. Use DEBUG to see every detection while tuning.",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=5.0,
        help="Seconds between status heartbeats. Set to 0 to disable.",
    )
    parser.add_argument(
        "--list-monitors",
        action="store_true",
        help="List available monitors for source configuration, then exit.",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=None,
        help="Force dry-run mode (log decisions, do not control OBS).",
    )
    mode_group.add_argument(
        "--live",
        dest="dry_run",
        action="store_false",
        help="Force live mode (control OBS via WebSocket).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.list_monitors:
        list_monitors()
        return

    run(args.config, dry_run_override=args.dry_run, status_interval=args.status_interval)


if __name__ == "__main__":
    main()
