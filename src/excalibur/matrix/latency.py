from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LatencyBudget:
    """Max-ms budgets per stage so direction never feels late.

    Values match the plan's targets. The routing engine and buses use these to
    reject or flag work that arrives older than allowed instead of applying late
    decisions.
    """

    ingest_to_frame_tap_ms: float = 80.0
    gsi_to_game_state_ms: float = 25.0
    game_state_to_intelligence_ms: float = 40.0
    frame_tap_to_agent_ms: float = 40.0
    agent_decision_ms: float = 80.0
    intent_to_engine_ms: float = 20.0
    engine_arbitration_ms: float = 30.0
    matrix_apply_ms: float = 80.0
    obs_command_ms: float = 50.0
    ui_update_ms: float = 150.0

    max_state_age_ms: float = 500.0
    end_to_end_data_ms: float = 250.0
    end_to_end_vision_ms: float = 500.0

    default_ttl_ms: dict[str, float] = field(
        default_factory=lambda: {
            "emergency": 10000.0,
            "gameplay": 3000.0,
            "replay": 8000.0,
            "stats": 5000.0,
            "automation": 15000.0,
        }
    )

    def ttl_for(self, category: str) -> float:
        return self.default_ttl_ms.get(category, self.default_ttl_ms["gameplay"])


@dataclass
class LatencySample:
    stage: str
    elapsed_ms: float
    budget_ms: float

    @property
    def within_budget(self) -> bool:
        return self.elapsed_ms <= self.budget_ms


class LatencyTracker:
    """Records per-stage timings and exposes the most recent samples for the UI."""

    def __init__(self, budget: LatencyBudget) -> None:
        self.budget = budget
        self.samples: dict[str, LatencySample] = {}
        self.breaches: int = 0

    def record(self, stage: str, elapsed_ms: float, budget_ms: float) -> LatencySample:
        sample = LatencySample(stage=stage, elapsed_ms=elapsed_ms, budget_ms=budget_ms)
        self.samples[stage] = sample
        if not sample.within_budget:
            self.breaches += 1
        return sample

    def snapshot(self) -> dict[str, dict[str, float | bool]]:
        return {
            stage: {
                "elapsed_ms": sample.elapsed_ms,
                "budget_ms": sample.budget_ms,
                "within_budget": sample.within_budget,
            }
            for stage, sample in self.samples.items()
        }
