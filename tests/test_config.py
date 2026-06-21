from pathlib import Path

from obs_auto_director.config import load_app_config, load_game_profile


def test_example_configs_load() -> None:
    root = Path(__file__).resolve().parents[1]

    app = load_app_config(root / "configs" / "app.example.yaml")
    profile = load_game_profile(root / "configs" / app.game_profile)

    assert app.obs.dry_run is True
    assert app.director.default_scene == "CS2 - Observer"
    assert profile.game == "Counter-Strike 2"
    assert {source.name for source in app.sources} == {"player_1", "player_2", "observer"}
