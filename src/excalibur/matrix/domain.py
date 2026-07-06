from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum

from excalibur.matrix.clock import now_ms

INPUT_PREFIX = "input_"
OUTPUT_PREFIX = "output_"
# Default surface size. Existing CS2 setups expect 11 inputs / 11 outputs, but
# the count is runtime-configurable; this is only the fallback when no explicit
# sizing is persisted.
DEFAULT_SLOTS = 11
# Backwards-compatible alias kept for older references.
MIN_SLOTS = DEFAULT_SLOTS


def new_id() -> str:
    return uuid.uuid4().hex


def input_slot_id(index: int) -> str:
    return f"{INPUT_PREFIX}{index:02d}"


def output_slot_id(index: int) -> str:
    return f"{OUTPUT_PREFIX}{index:02d}"


class SlotState(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    MISSING_SIGNAL = "missing_signal"
    RESERVED = "reserved"
    LOCKED = "locked"
    DEGRADED = "degraded"


class OutputKind(str, Enum):
    PROGRAM = "program"
    PREVIEW = "preview"
    OBS_SCENE = "obs_scene"
    REPLAY = "replay"
    MULTIVIEW = "multiview"
    CLEAN = "clean"
    RESERVED = "reserved"


class BusName(str, Enum):
    DECISION = "decision"
    CONTROL = "control"
    RULE = "rule"
    GAME_STATE = "game_state"
    INTELLIGENCE = "intelligence"


@dataclass
class InputSlot:
    id: str
    label: str
    state: SlotState = SlotState.INACTIVE
    protocol: str | None = None
    uri: str | None = None
    player_name: str | None = None
    team: str | None = None
    role: str | None = None
    last_frame_at: float | None = None

    @property
    def has_signal(self) -> bool:
        return self.state in (SlotState.ACTIVE, SlotState.DEGRADED)


@dataclass
class OutputSlot:
    id: str
    label: str
    kind: OutputKind = OutputKind.RESERVED
    state: SlotState = SlotState.INACTIVE
    obs_scene: str | None = None
    uri: str | None = None


@dataclass
class Route:
    output_id: str
    input_id: str | None
    set_by: str
    reason: str
    set_at: float = field(default_factory=now_ms)


@dataclass
class OutputLock:
    output_id: str
    locked_by: str
    reason: str
    until_ms: float | None = None
    created_at: float = field(default_factory=now_ms)

    def is_active(self, now: float | None = None) -> bool:
        if self.until_ms is None:
            return True
        return (now if now is not None else now_ms()) < self.until_ms


@dataclass
class RoutingIntent:
    """A proposal to change routing.

    Agents, operators, and automation rules publish these. Only the routing
    engine may turn an intent into an applied route.
    """

    producer: str
    bus: BusName
    target_output: str
    candidate_input: str | None
    reason: str
    priority: int = 0
    confidence: float = 1.0
    ttl_ms: float = 3000.0
    interrupt: bool = False
    hold_ms: float = 0.0
    intent_id: str = field(default_factory=new_id)
    created_at: float = field(default_factory=now_ms)

    def age_ms(self, now: float | None = None) -> float:
        return (now if now is not None else now_ms()) - self.created_at

    def is_stale(self, now: float | None = None) -> bool:
        return self.age_ms(now) > self.ttl_ms


class ControlAction(str, Enum):
    MANUAL_CUT = "manual_cut"
    LOCK_OUTPUT = "lock_output"
    RELEASE_LOCK = "release_lock"
    HOLD_SOURCE = "hold_source"
    EMERGENCY_ROUTE = "emergency_route"
    TAKE_NEXT = "take_next"


@dataclass
class ControlCommand:
    action: ControlAction
    operator: str
    target_output: str
    candidate_input: str | None = None
    reason: str = ""
    hold_ms: float = 0.0
    lock_ms: float | None = None
    command_id: str = field(default_factory=new_id)
    created_at: float = field(default_factory=now_ms)


class DecisionOutcome(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DELAYED = "delayed"
    SUPERSEDED = "superseded"


@dataclass
class RoutingDecision:
    outcome: DecisionOutcome
    output_id: str
    input_id: str | None
    producer: str
    reason: str
    intent_id: str | None = None
    age_ms: float = 0.0
    decision_ms: float = 0.0
    decision_id: str = field(default_factory=new_id)
    created_at: float = field(default_factory=now_ms)


@dataclass
class PlayerState:
    slot: str
    name: str
    team: str
    health: int = 100
    armor: int = 0
    alive: bool = True
    flashed: int = 0
    burning: int = 0
    active_weapon: str | None = None
    weapons: list[str] = field(default_factory=list)
    money: int = 0
    position: tuple[float, float, float] | None = None
    input_id: str | None = None


@dataclass
class BombState:
    state: str = "carried"  # carried, dropped, planted, defusing, defused, exploded
    site: str | None = None
    countdown_ms: float | None = None


@dataclass
class MatchState:
    round_id: str
    phase: str  # freezetime, live, over, warmup, unknown
    players: dict[str, PlayerState] = field(default_factory=dict)
    bomb: BombState = field(default_factory=BombState)
    round_number: int = 0
    map_name: str | None = None
    state_version: int = 0
    game_timestamp: float = 0.0
    received_at: float = field(default_factory=now_ms)

    def age_ms(self, now: float | None = None) -> float:
        return (now if now is not None else now_ms()) - self.received_at

    def alive_players(self) -> list[PlayerState]:
        return [player for player in self.players.values() if player.alive]

    def alive_by_team(self) -> dict[str, list[PlayerState]]:
        teams: dict[str, list[PlayerState]] = {}
        for player in self.alive_players():
            teams.setdefault(player.team, []).append(player)
        return teams


@dataclass
class GameStateEvent:
    type: str
    player_slot: str | None = None
    data: dict = field(default_factory=dict)
    round_id: str | None = None
    event_id: str = field(default_factory=new_id)
    created_at: float = field(default_factory=now_ms)


@dataclass
class MatchIntelligenceEvent:
    type: str
    round_id: str
    priority: int
    confidence: float
    ttl_ms: float
    affected_players: list[str] = field(default_factory=list)
    recommended_inputs: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    event_id: str = field(default_factory=new_id)
    created_at: float = field(default_factory=now_ms)

    def age_ms(self, now: float | None = None) -> float:
        return (now if now is not None else now_ms()) - self.created_at

    def is_stale(self, now: float | None = None) -> bool:
        return self.age_ms(now) > self.ttl_ms
