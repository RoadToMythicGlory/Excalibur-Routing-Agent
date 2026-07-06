from __future__ import annotations

import logging
from typing import Any, Iterable

from excalibur.config import ObsConfig

logger = logging.getLogger(__name__)


class ObsSceneRouter:
    """Isolates all OBS control behind a small surface.

    Keeping OBS access in one place means a future OBS plugin (or a different
    transport) can replace this class without touching detection or director
    logic.
    """

    def __init__(self, config: ObsConfig) -> None:
        self.config = config
        self.client: Any | None = None

    def connect(self, expected_scenes: Iterable[str] | None = None) -> None:
        expected = {scene for scene in (expected_scenes or []) if scene}

        if self.config.dry_run:
            logger.info("OBS dry-run mode enabled; scene changes will be logged only.")
            if expected:
                logger.info("Scenes this profile will use: %s.", ", ".join(sorted(expected)))
            return

        try:
            from obsws_python import ReqClient
        except ImportError as error:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "obsws-python is required for live OBS control. "
                "Install it with: python -m pip install obsws-python"
            ) from error

        self.client = ReqClient(
            host=self.config.host,
            port=self.config.port,
            password=self.config.password,
            timeout=3,
        )
        logger.info("Connected to OBS WebSocket at %s:%s.", self.config.host, self.config.port)
        self._verify_scenes(expected)

    def switch_scene(self, scene_name: str, reason: str) -> None:
        if self.config.dry_run:
            logger.info("[DRY RUN] Would switch OBS scene to %r (%s).", scene_name, reason)
            return

        if self.client is None:
            raise RuntimeError("OBS client is not connected.")

        logger.info("Switching OBS scene to %r (%s).", scene_name, reason)
        self.client.set_current_program_scene(name=scene_name)

    def _verify_scenes(self, expected: set[str]) -> None:
        if not expected or self.client is None:
            return

        try:
            available = self.available_scenes()
        except Exception as error:  # pragma: no cover - network/runtime dependent
            logger.warning("Could not fetch OBS scene list for verification: %s", error)
            return

        missing = expected - available
        if missing:
            logger.warning(
                "These configured scenes are not present in OBS: %s. "
                "Scene switches to them will fail until they are created.",
                ", ".join(sorted(missing)),
            )
        else:
            logger.info("All configured scenes exist in OBS.")

    def available_scenes(self) -> set[str]:
        if self.client is None:
            return set()
        response = self.client.get_scene_list()
        return {scene["sceneName"] for scene in response.scenes}
