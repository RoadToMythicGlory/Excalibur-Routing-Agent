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


RuleConfig = Annotated[
    TemplateRuleConfig
    | RegionBrightnessRuleConfig
    | RegionColorRuleConfig
    | MotionRuleConfig,
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
    rules: list[RuleConfig]


class GameProfile(BaseModel):
    game: str
    events: list[EventConfig]

    @model_validator(mode="after")
    def require_events(self) -> "GameProfile":
        if not self.events:
            raise ValueError("At least one game event is required.")
        names = [event.name for event in self.events]
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
