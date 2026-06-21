from __future__ import annotations

from pathlib import Path
from typing import Annotated
from typing import Literal

import yaml
from pydantic import BaseModel, Field, PositiveFloat, PositiveInt, model_validator


Rect = tuple[int, int, int, int]


class ObsConfig(BaseModel):
    host: str = "127.0.0.1"
    port: PositiveInt = 4455
    password: str | None = None
    dry_run: bool = True


class SourceConfig(BaseModel):
    name: str
    monitor: PositiveInt = 1
    fps: PositiveFloat = 8.0
    crop: Rect | None = None


class DirectorConfig(BaseModel):
    default_scene: str
    min_confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    switch_cooldown_sec: PositiveFloat = 2.0
    fallback_after_sec: PositiveFloat = 5.0
    preempt_priority_gap: int = Field(default=25, ge=0)
    recent_event_ttl_sec: PositiveFloat = 4.0


class AppConfig(BaseModel):
    obs: ObsConfig
    director: DirectorConfig
    game_profile: str
    sources: list[SourceConfig]

    @model_validator(mode="after")
    def require_sources(self) -> "AppConfig":
        if not self.sources:
            raise ValueError("At least one capture source is required.")
        names = [source.name for source in self.sources]
        if len(names) != len(set(names)):
            raise ValueError("Capture source names must be unique.")
        return self


class TemplateRuleConfig(BaseModel):
    type: Literal["template"]
    source: str
    region: Rect | None = None
    template_path: str
    threshold: float = Field(default=0.8, ge=0.0, le=1.0)


class RegionBrightnessRuleConfig(BaseModel):
    type: Literal["region_brightness"]
    source: str
    region: Rect
    operator: Literal["above", "below"]
    threshold: float = Field(ge=0.0, le=255.0)


class RegionColorRuleConfig(BaseModel):
    type: Literal["region_color"]
    source: str
    region: Rect
    lower_bgr: tuple[int, int, int]
    upper_bgr: tuple[int, int, int]
    min_ratio: float = Field(default=0.2, ge=0.0, le=1.0)


class MotionRuleConfig(BaseModel):
    type: Literal["motion"]
    source: str
    region: Rect | None = None
    threshold: float = Field(default=20.0, ge=0.0)
    min_ratio: float = Field(default=0.05, ge=0.0, le=1.0)


class KillFeedChangeRuleConfig(BaseModel):
    """Detects new kill-feed entries by measuring change in the kill-feed region."""

    type: Literal["kill_feed_change"]
    source: str
    region: Rect
    min_new_entries: PositiveInt = 1
    # Fraction of the region that must change to count as one new feed entry.
    change_ratio_per_entry: float = Field(default=0.012, ge=0.0, le=1.0)
    pixel_threshold: float = Field(default=28.0, ge=0.0, le=255.0)


class KillFeedMultiRuleConfig(BaseModel):
    """Detects several kill-feed entries inside a rolling time window."""

    type: Literal["kill_feed_multi"]
    source: str
    region: Rect
    same_player_kills: PositiveInt = 2
    window_sec: PositiveFloat = 5.0
    change_ratio_per_entry: float = Field(default=0.012, ge=0.0, le=1.0)
    pixel_threshold: float = Field(default=28.0, ge=0.0, le=255.0)


class AliveCountRuleConfig(BaseModel):
    """Clutch/alive-count rule.

    Alive counts cannot be derived reliably from a single observer frame, so
    this rule consults an external game-state provider when one is wired in
    (see ``RuleEvaluator.set_alive_counts``). Without that provider it stays
    dormant rather than firing on noise.
    """

    type: Literal["alive_count"]
    source: str
    team_a_alive: int | None = Field(default=None, ge=0)
    team_b_alive: int | None = Field(default=None, ge=0)
    one_side_alive: int | None = Field(default=None, ge=0)
    other_side_alive: int | None = Field(default=None, ge=0)


RuleConfig = Annotated[
    TemplateRuleConfig
    | RegionBrightnessRuleConfig
    | RegionColorRuleConfig
    | MotionRuleConfig
    | KillFeedChangeRuleConfig
    | KillFeedMultiRuleConfig
    | AliveCountRuleConfig,
    Field(discriminator="type"),
]


class EventConfig(BaseModel):
    name: str
    scene: str
    category: str = "general"
    description: str | None = None
    priority: int = 0
    hold_sec: PositiveFloat = 2.0
    stable_frames: PositiveInt = 2
    rule_mode: Literal["any", "all"] = "any"
    interrupt: bool = False
    # Optional routing hints. ``source`` is the feed the event watches; a
    # ``target_source`` or ``scene`` of "dynamic" is resolved at runtime to the
    # feed that actually triggered the event (via the profile ``sources`` map).
    source: str | None = None
    target_source: str | None = None
    rules: list[RuleConfig]


class RuntimeConfig(BaseModel):
    config_type: str | None = None
    resolution: tuple[int, int] | None = None
    color_space: str = "BGR"
    note: str | None = None


class ProfileDefaults(BaseModel):
    fallback_scene: str | None = None
    decision_tick_ms: PositiveInt = 250
    min_cut_gap_sec: PositiveFloat = 1.2
    default_hold_sec: PositiveFloat = 3.0
    max_hold_sec: PositiveFloat = 8.0
    stable_frames: PositiveInt = 2
    rule_mode: Literal["any", "all"] = "any"
    return_to_observer_after_idle_sec: PositiveFloat = 2.0


class MatrixSection(BaseModel):
    enabled: bool = False
    program_output: str | None = None
    preview_output: str | None = None
    multiview_output: str | None = None
    clean_output: str | None = None


class SourceProfile(BaseModel):
    kind: str
    scene: str
    output_route_source: str | None = None


class ScoringDecay(BaseModel):
    kill_sec: PositiveFloat = 6.0
    objective_sec: PositiveFloat = 8.0
    utility_sec: PositiveFloat = 3.0
    combat_sec: PositiveFloat = 2.0
    clutch_sec: PositiveFloat = 10.0


class ScoringConfig(BaseModel):
    enabled: bool = True
    weights: dict[str, float] = Field(default_factory=dict)
    decay: ScoringDecay = Field(default_factory=ScoringDecay)


class CutRuleConfig(BaseModel):
    name: str
    type: Literal[
        "cooldown",
        "interrupt_priority",
        "score_margin",
        "fallback",
        "source_health_gate",
    ]
    min_gap_sec: float | None = Field(default=None, ge=0.0)
    min_priority: int | None = None
    margin: float | None = Field(default=None, ge=0.0)
    scene: str | None = None
    after_idle_sec: float | None = Field(default=None, ge=0.0)
    require_healthy: bool | None = None


class GameProfile(BaseModel):
    game: str
    version: str | None = None
    runtime: RuntimeConfig | None = None
    defaults: ProfileDefaults = Field(default_factory=ProfileDefaults)
    matrix: MatrixSection | None = None
    sources: dict[str, SourceProfile] = Field(default_factory=dict)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    cut_rules: list[CutRuleConfig] = Field(default_factory=list)
    observer_events: list[EventConfig] = Field(default_factory=list)
    events: list[EventConfig] = Field(default_factory=list)

    @property
    def all_events(self) -> list[EventConfig]:
        """Observer-side events first, then per-source events."""
        return [*self.observer_events, *self.events]

    def scene_for_source(self, source: str | None) -> str | None:
        if source is None:
            return None
        profile = self.sources.get(source)
        return profile.scene if profile is not None else None

    @model_validator(mode="after")
    def require_events(self) -> "GameProfile":
        events = self.all_events
        if not events:
            raise ValueError("At least one game event is required.")
        names = [event.name for event in events]
        if len(names) != len(set(names)):
            raise ValueError("Game event names must be unique.")
        return self


def load_yaml(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}.")
    return data


def load_app_config(path: str | Path) -> AppConfig:
    return AppConfig.model_validate(load_yaml(path))


def load_game_profile(path: str | Path) -> GameProfile:
    return GameProfile.model_validate(load_yaml(path))
