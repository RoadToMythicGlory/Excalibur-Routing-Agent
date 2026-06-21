from pathlib import Path

from obs_auto_director.config import (
    AppConfig,
    DirectorConfig,
    EventConfig,
    GameProfile,
    MotionRuleConfig,
    ObsConfig,
    SourceConfig,
)
from obs_auto_director.main import collect_expected_scenes
from obs_auto_director.obs.client import ObsSceneRouter


class FakeResponse:
    def __init__(self, scene_names: list[str]) -> None:
        self.scenes = [{"sceneName": name} for name in scene_names]


class FakeReqClient:
    def __init__(self, scene_names: list[str]) -> None:
        self._scene_names = scene_names
        self.switched_to: list[str] = []

    def get_scene_list(self) -> FakeResponse:
        return FakeResponse(self._scene_names)

    def set_current_program_scene(self, name: str) -> None:
        self.switched_to.append(name)


def test_dry_run_router_does_not_need_client() -> None:
    router = ObsSceneRouter(ObsConfig(dry_run=True))
    router.connect(expected_scenes={"CS2 - Observer"})
    router.switch_scene("CS2 - Player 1", "test")

    assert router.client is None


def test_live_router_switches_with_positional_scene_name() -> None:
    router = ObsSceneRouter(ObsConfig(dry_run=False))
    router.client = FakeReqClient(["CS2 - Observer", "CS2 - Player 1"])

    router.switch_scene("CS2 - Player 1", "cs2_event:low_health")

    assert router.client.switched_to == ["CS2 - Player 1"]
    assert router.available_scenes() == {"CS2 - Observer", "CS2 - Player 1"}


def test_collect_expected_scenes_includes_default_and_events() -> None:
    app_config = AppConfig(
        obs=ObsConfig(dry_run=True),
        director=DirectorConfig(default_scene="CS2 - Observer"),
        game_profile="games/cs2.yaml",
        sources=[SourceConfig(name="player_1")],
    )
    profile = GameProfile(
        game="Counter-Strike 2",
        events=[
            EventConfig(
                name="player_1_active_combat",
                scene="CS2 - Player 1",
                rules=[
                    MotionRuleConfig(
                        type="motion",
                        source="player_1",
                        region=(0, 0, 10, 10),
                    )
                ],
            )
        ],
    )

    assert collect_expected_scenes(app_config, profile) == {
        "CS2 - Observer",
        "CS2 - Player 1",
    }


def test_example_configs_expected_scenes_match_profile() -> None:
    from obs_auto_director.config import load_app_config, load_game_profile

    root = Path(__file__).resolve().parents[1]
    app = load_app_config(root / "configs" / "app.example.yaml")
    profile = load_game_profile(root / "configs" / app.game_profile)

    expected = collect_expected_scenes(app, profile)
    assert "CS2 - Observer" in expected
    assert "CS2 - Player 1" in expected
    assert "CS2 - Player 2" in expected
