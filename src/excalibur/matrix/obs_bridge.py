from __future__ import annotations

import logging

from excalibur.matrix.clock import now_ms
from excalibur.matrix.domain import Route, RoutingDecision
from excalibur.obs.client import ObsSceneRouter

logger = logging.getLogger(__name__)


class ObsBridge:
    """Connects matrix outputs to OBS.

    OBS consumes stable matrix outputs and stays out of the "observe all inputs"
    path. When the routing engine applies a route to the program output, the
    bridge switches the matching OBS scene through OBS WebSocket and records the
    command round-trip for the latency budget.

    Scene resolution order:
      1. Per-input scene mapping (one OBS scene fed by each input).
      2. The output slot's own `obs_scene`.
    """

    def __init__(
        self,
        router: ObsSceneRouter,
        program_output: str = "output_01",
        input_scene_map: dict[str, str] | None = None,
        output_scene_map: dict[str, str] | None = None,
    ) -> None:
        self.router = router
        self.program_output = program_output
        self.input_scene_map = input_scene_map or {}
        self.output_scene_map = output_scene_map or {}
        self.last_command_ms: float = 0.0
        self.current_scene: str | None = None

    def expected_scenes(self) -> set[str]:
        scenes: set[str] = set(self.input_scene_map.values())
        scenes.update(self.output_scene_map.values())
        return {scene for scene in scenes if scene}

    def connect(self) -> None:
        self.router.connect(expected_scenes=self.expected_scenes())

    def on_route_applied(self, route: Route, decision: RoutingDecision) -> None:
        if route.output_id != self.program_output:
            return
        scene = self._scene_for(route)
        if scene is None:
            logger.warning(
                "No OBS scene mapped for output=%s input=%s; skipping switch",
                route.output_id,
                route.input_id,
            )
            return
        start = now_ms()
        self.router.switch_scene(scene, reason=decision.reason)
        self.last_command_ms = now_ms() - start
        self.current_scene = scene

    def _scene_for(self, route: Route) -> str | None:
        if route.input_id and route.input_id in self.input_scene_map:
            return self.input_scene_map[route.input_id]
        return self.output_scene_map.get(route.output_id)
