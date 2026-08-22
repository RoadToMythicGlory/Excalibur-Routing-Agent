from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SoccerSourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structured_heartbeat_ms: float = 1000.0
    structured_freshness_ms: float = 1500.0
    structured_stale_ms: float = 2500.0
    vision_freshness_ms: float = 800.0
    max_future_skew_ms: float = 400.0


class SoccerRuntimeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = 320
    fps: float = 2.5
    batch: int = 1
    provider: str = "CPUExecutionProvider"
    stagger: bool = True


class SoccerInferenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_profile: str = "auto"
    cpu: SoccerRuntimeProfile = Field(default_factory=SoccerRuntimeProfile)
    gpu: SoccerRuntimeProfile = Field(
        default_factory=lambda: SoccerRuntimeProfile(
            width=540,
            fps=6.0,
            batch=4,
            provider="CUDAExecutionProvider",
            stagger=False,
        )
    )
    shadow_until_validated: bool = True
    model_manifest: str | None = None


class SoccerGatingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_event_confidence: float = 0.62
    min_role_confidence: float = 0.55
    start_debounce_ms: float = 350.0
    end_debounce_ms: float = 800.0
    failsafe_max_event_ms: float = 25000.0
    cut_cooldown_ms: float = 700.0
    min_dwell_ms: float = 1200.0
    score_margin: float = 12.0
    ordinary_pass_not_event: bool = True


class SoccerPhasePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_roles: list[str]
    fallback_roles: list[str] = Field(default_factory=list)


class SoccerEventPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int
    hold_ms: float
    ttl_ms: float
    interrupt: bool = False
    max_duration_ms: float = 20000.0
    phases: dict[str, SoccerPhasePlan] = Field(min_length=1)

    @field_validator("phases")
    @classmethod
    def _require_setup_phase(
        cls, value: dict[str, SoccerPhasePlan]
    ) -> dict[str, SoccerPhasePlan]:
        # The director falls back to the setup plan for unknown phases; a
        # profile without one would otherwise fail mid-broadcast, at cut time.
        if "setup" not in value:
            raise ValueError("event plan must define a 'setup' phase")
        return value


class SoccerCameraRoleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    pin_main: bool = False


class SoccerReturnToMainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    after_end_ms: float = 400.0
    priority: int = 20
    confidence: float = 0.95


class SoccerManualSuspensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on_operator_route: bool = True
    persist: bool = True
    resume_action: Literal["explicit_resume"] = "explicit_resume"


class SoccerEvaluationGates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_precision: float = 0.8
    min_recall: float = 0.7
    max_false_events_per_match: float = 4.0
    max_inference_ms: float = 180.0
    max_switch_rate_per_min: float = 18.0


class SoccerProfile(BaseModel):
    """Validated soccer director behavior profile."""

    model_config = ConfigDict(extra="forbid")

    sport: str = "soccer"
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    game: str = "Soccer"
    main_input: str = "input_01"
    program_output: str = "output_01"
    sources: SoccerSourcesConfig = Field(default_factory=SoccerSourcesConfig)
    inference: SoccerInferenceConfig = Field(default_factory=SoccerInferenceConfig)
    gating: SoccerGatingConfig = Field(default_factory=SoccerGatingConfig)
    events: dict[str, SoccerEventPlan]
    camera_roles: list[SoccerCameraRoleConfig]

    @field_validator("camera_roles")
    @classmethod
    def _known_role_ids(
        cls, value: list[SoccerCameraRoleConfig]
    ) -> list[SoccerCameraRoleConfig]:
        from excalibur.matrix.soccer.domain import CameraRole

        known = {role.value for role in CameraRole}
        unknown = [entry.id for entry in value if entry.id not in known]
        if unknown:
            raise ValueError(f"unknown camera role ids: {unknown}")
        return value

    def pinned_main_role(self) -> str:
        for entry in self.camera_roles:
            if entry.pin_main:
                return entry.id
        from excalibur.matrix.soccer.domain import CameraRole

        return CameraRole.MAIN_WIDE.value
    return_to_main: SoccerReturnToMainConfig = Field(
        default_factory=SoccerReturnToMainConfig
    )
    manual_suspension: SoccerManualSuspensionConfig = Field(
        default_factory=SoccerManualSuspensionConfig
    )
    evaluation_gates: SoccerEvaluationGates = Field(
        default_factory=SoccerEvaluationGates
    )

    def event_plan(self, event_type: str) -> SoccerEventPlan:
        return self.events[event_type]


def default_soccer_profile_path() -> Path:
    return Path("configs/games/soccer.json")


def load_soccer_profile(path: str | Path | None = None) -> SoccerProfile:
    target = Path(path) if path else default_soccer_profile_path()
    data = json.loads(target.read_text(encoding="utf-8"))
    return SoccerProfile.model_validate(data)
