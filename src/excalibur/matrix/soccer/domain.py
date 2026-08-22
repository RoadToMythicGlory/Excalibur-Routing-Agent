from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from excalibur.matrix.clock import now_ms
from excalibur.matrix.domain import new_id


class SoccerEventType(str, Enum):
    FOUL = "foul"
    FREE_KICK = "free_kick"
    PENALTY_KICK = "penalty_kick"
    CORNER_KICK = "corner_kick"
    BURST_ATTACK = "burst_attack"


class SoccerObservationKind(str, Enum):
    HEARTBEAT = "heartbeat"
    EVENT = "event"
    ORDINARY_PASS = "ordinary_pass"
    OPEN_PLAY = "open_play"
    CAMERA_ROLE = "camera_role"


class SoccerRevision(str, Enum):
    START = "start"
    UPDATE = "update"
    END = "end"


class SoccerPhase(str, Enum):
    SETUP = "setup"
    EXECUTION = "execution"
    FOLLOW_THROUGH = "follow_through"


class EventLifecycle(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    RESOLVING = "resolving"
    ENDED = "ended"


class CameraRole(str, Enum):
    MAIN_WIDE = "main_wide"
    TACTICAL_HIGH = "tactical_high"
    SIDELINE_TIGHT = "sideline_tight"
    GOAL_LINE = "goal_line"
    BEHIND_GOAL_LEFT = "behind_goal_left"
    BEHIND_GOAL_RIGHT = "behind_goal_right"
    REACTION = "reaction"
    UNKNOWN = "unknown"


class SourceAuthority(str, Enum):
    STRUCTURED = "structured"
    VISION = "vision"
    NONE = "none"


class DirectorMode(str, Enum):
    AUTOMATIC = "automatic"
    MANUAL_SUSPENDED = "manual_suspended"
    DISABLED = "disabled"


SPECIAL_EVENT_TYPES = {item.value for item in SoccerEventType}
FOUL_TRANSITIONS = {SoccerEventType.FREE_KICK.value, SoccerEventType.PENALTY_KICK.value}


@dataclass
class PitchPoint:
    x: float = 0.5
    y: float = 0.5


@dataclass
class SoccerHeartbeat:
    match_id: str
    timestamp_ms: float
    sequence: int
    period: int | None = None
    clock_mmss: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    source: str = "structured"
    confidence: float = 1.0
    received_at: float = field(default_factory=now_ms)


@dataclass
class SoccerEventRevision:
    kind: str
    revision: str
    event_id: str
    match_id: str
    event_type: str
    timestamp_ms: float
    sequence: int
    confidence: float
    correlation_id: str | None = None
    phase: str = SoccerPhase.SETUP.value
    source: str = "structured"
    team: str | None = None
    opponent: str | None = None
    player: str | None = None
    zone: str = "unknown"
    ball: PitchPoint | None = None
    action: str | None = None
    evidence: list[str] = field(default_factory=list)
    recommended_inputs: list[str] = field(default_factory=list)
    received_at: float = field(default_factory=now_ms)

    def correlation(self) -> str:
        return self.correlation_id or self.event_id


@dataclass
class VisionObservation:
    kind: str
    confidence: float
    event_type: str | None = None
    phase: str | None = None
    input_id: str | None = None
    role: str | None = None
    zone: str | None = None
    ball: PitchPoint | None = None
    evidence: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=now_ms)


@dataclass
class CameraAssignment:
    input_id: str
    role: str
    confidence: float
    source: str
    score: float = 0.0
    visible_ball: bool = False
    sharpness: float = 0.5
    occlusion: float = 0.0
    coverage: float = 0.5
    latency_ms: float = 0.0


@dataclass
class SoccerEventState:
    event_id: str
    correlation_id: str
    event_type: str
    lifecycle: str
    phase: str
    source: str
    confidence: float
    match_id: str
    team: str | None = None
    player: str | None = None
    zone: str = "unknown"
    ball: PitchPoint | None = None
    evidence: list[str] = field(default_factory=list)
    recommended_inputs: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=now_ms)
    updated_at: float = field(default_factory=now_ms)
    candidate_since: float | None = None
    resolving_since: float | None = None
    last_sequence: int = 0

    def age_ms(self, now: float | None = None) -> float:
        return (now if now is not None else now_ms()) - self.started_at


@dataclass
class SoccerMatchView:
    match_id: str | None
    authority: str
    heartbeat_fresh: bool
    vision_fresh: bool
    fallback_reason: str | None
    event: SoccerEventState | None
    cameras: dict[str, CameraAssignment] = field(default_factory=dict)
    profile_version: str = "1.0.0"
    model_version: str | None = None
    updated_at: float = field(default_factory=now_ms)

    def snapshot(self) -> dict:
        event = self.event
        return {
            "match_id": self.match_id,
            "authority": self.authority,
            "heartbeat_fresh": self.heartbeat_fresh,
            "vision_fresh": self.vision_fresh,
            "fallback_reason": self.fallback_reason,
            "profile_version": self.profile_version,
            "model_version": self.model_version,
            "event": None
            if event is None
            else {
                "event_id": event.event_id,
                "correlation_id": event.correlation_id,
                "type": event.event_type,
                "lifecycle": event.lifecycle,
                "phase": event.phase,
                "source": event.source,
                "confidence": event.confidence,
                "age_ms": event.age_ms(),
                "zone": event.zone,
                "team": event.team,
                "player": event.player,
                "evidence": list(event.evidence),
            },
            "cameras": {
                input_id: {
                    "role": assignment.role,
                    "confidence": assignment.confidence,
                    "source": assignment.source,
                    "score": assignment.score,
                }
                for input_id, assignment in self.cameras.items()
            },
        }


def new_event_id() -> str:
    return new_id()
