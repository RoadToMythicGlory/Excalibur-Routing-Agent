from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Callable

from obs_auto_director.matrix.buses import MatrixBuses
from obs_auto_director.matrix.clock import now_ms
from obs_auto_director.matrix.domain import (
    ControlAction,
    ControlCommand,
    DecisionOutcome,
    OutputLock,
    Route,
    RoutingDecision,
    RoutingIntent,
)
from obs_auto_director.matrix.latency import LatencyBudget
from obs_auto_director.matrix.matrix_core import MatrixCore

logger = logging.getLogger(__name__)

RouteAppliedCallback = Callable[[Route, RoutingDecision], None]


@dataclass
class _OutputRuntime:
    current_priority: int = 0
    current_confidence: float = 0.0
    current_producer: str = "system"
    last_switch_at: float = 0.0
    hold_until: float = 0.0


class RoutingEngine:
    """The single authority that turns intents into applied routes.

    Authority order:
      1. Emergency operator commands.
      2. Output locks / manual holds.
      3. Safety / source-health constraints.
      4. Automation rules.
      5. Agent decisions by priority, confidence, TTL, and recency.
    """

    def __init__(
        self,
        matrix: MatrixCore,
        buses: MatrixBuses,
        budget: LatencyBudget | None = None,
        switch_cooldown_ms: float = 600.0,
        preempt_priority_gap: int = 15,
        on_route_applied: RouteAppliedCallback | None = None,
        audit_size: int = 500,
    ) -> None:
        self.matrix = matrix
        self.buses = buses
        self.budget = budget or LatencyBudget()
        self.switch_cooldown_ms = switch_cooldown_ms
        self.preempt_priority_gap = preempt_priority_gap
        self.on_route_applied = on_route_applied
        self.locks: dict[str, OutputLock] = {}
        self.runtime: dict[str, _OutputRuntime] = {}
        self.audit: deque[RoutingDecision] = deque(maxlen=audit_size)
        self.decisions_emitted = 0

        buses.decision.subscribe(self.handle_intent)
        buses.rule.subscribe(self.handle_intent)
        buses.control.subscribe(self.handle_control)

    # -- intents from agents and automation rules ---------------------------
    def handle_intent(self, intent: RoutingIntent) -> RoutingDecision:
        start = now_ms()
        runtime = self.runtime.setdefault(intent.target_output, _OutputRuntime())

        if intent.target_output not in self.matrix.outputs:
            return self._reject(intent, "unknown_output", start)

        age = intent.age_ms(start)
        if intent.is_stale(start):
            return self._reject(intent, f"stale_intent age_ms={age:.0f}>{intent.ttl_ms:.0f}", start)

        lock = self._active_lock(intent.target_output, start)
        if lock is not None:
            return self._reject(intent, f"output_locked_by:{lock.locked_by}", start)

        if intent.candidate_input is not None and not self.matrix.input_available(
            intent.candidate_input
        ):
            return self._reject(intent, f"source_unavailable:{intent.candidate_input}", start)

        if intent.candidate_input == self.matrix.current_input_for(intent.target_output):
            if not intent.interrupt:
                runtime.hold_until = max(runtime.hold_until, start + intent.hold_ms)
            return self._record(
                intent, DecisionOutcome.SUPERSEDED, "already_routed", start, applied=False
            )

        in_hold = start < runtime.hold_until
        can_preempt = intent.interrupt or intent.priority >= (
            runtime.current_priority + self.preempt_priority_gap
        )
        if in_hold and not can_preempt:
            return self._record(
                intent,
                DecisionOutcome.DELAYED,
                f"holding priority={runtime.current_priority} until_hold",
                start,
                applied=False,
            )

        bypass_cooldown = in_hold and can_preempt
        if not bypass_cooldown and start - runtime.last_switch_at < self.switch_cooldown_ms:
            return self._record(
                intent, DecisionOutcome.DELAYED, "switch_cooldown", start, applied=False
            )

        return self._accept(intent, runtime, start)

    # -- operator commands ---------------------------------------------------
    def handle_control(self, command: ControlCommand) -> RoutingDecision:
        start = now_ms()
        runtime = self.runtime.setdefault(command.target_output, _OutputRuntime())

        if command.action == ControlAction.LOCK_OUTPUT:
            until = start + command.lock_ms if command.lock_ms else None
            self.locks[command.target_output] = OutputLock(
                output_id=command.target_output,
                locked_by=command.operator,
                reason=command.reason or "operator_lock",
                until_ms=until,
            )
            return self._control_decision(
                command, DecisionOutcome.ACCEPTED, "output_locked", start
            )

        if command.action == ControlAction.RELEASE_LOCK:
            self.locks.pop(command.target_output, None)
            return self._control_decision(
                command, DecisionOutcome.ACCEPTED, "lock_released", start
            )

        if command.action == ControlAction.HOLD_SOURCE:
            runtime.hold_until = start + command.hold_ms
            return self._control_decision(
                command, DecisionOutcome.ACCEPTED, "source_held", start
            )

        if command.action in (
            ControlAction.MANUAL_CUT,
            ControlAction.EMERGENCY_ROUTE,
            ControlAction.TAKE_NEXT,
        ):
            emergency = command.action == ControlAction.EMERGENCY_ROUTE
            if command.candidate_input is not None and not self.matrix.input_available(
                command.candidate_input
            ):
                return self._control_decision(
                    command,
                    DecisionOutcome.REJECTED,
                    f"source_unavailable:{command.candidate_input}",
                    start,
                )
            # Operator commands override agent locks/holds except other operators' locks.
            lock = self._active_lock(command.target_output, start)
            if lock is not None and not emergency:
                return self._control_decision(
                    command,
                    DecisionOutcome.REJECTED,
                    f"output_locked_by:{lock.locked_by}",
                    start,
                )
            route = self.matrix.apply_route(
                command.target_output,
                command.candidate_input,
                set_by=f"operator:{command.operator}",
                reason=command.reason or command.action.value,
            )
            runtime.current_priority = 1000 if emergency else 500
            runtime.current_confidence = 1.0
            runtime.current_producer = f"operator:{command.operator}"
            runtime.last_switch_at = start
            runtime.hold_until = start + command.hold_ms
            decision = self._control_decision(
                command, DecisionOutcome.ACCEPTED, command.action.value, start
            )
            if self.on_route_applied is not None:
                self.on_route_applied(route, decision)
            return decision

        return self._control_decision(command, DecisionOutcome.REJECTED, "unknown_action", start)

    # -- helpers -------------------------------------------------------------
    def _accept(
        self, intent: RoutingIntent, runtime: _OutputRuntime, start: float
    ) -> RoutingDecision:
        route = self.matrix.apply_route(
            intent.target_output,
            intent.candidate_input,
            set_by=intent.producer,
            reason=intent.reason,
        )
        runtime.current_priority = intent.priority
        runtime.current_confidence = intent.confidence
        runtime.current_producer = intent.producer
        runtime.last_switch_at = start
        runtime.hold_until = start + intent.hold_ms
        decision = self._record(
            intent, DecisionOutcome.ACCEPTED, intent.reason, start, applied=True
        )
        if self.on_route_applied is not None:
            self.on_route_applied(route, decision)
        return decision

    def _active_lock(self, output_id: str, now: float) -> OutputLock | None:
        lock = self.locks.get(output_id)
        if lock is None:
            return None
        if not lock.is_active(now):
            self.locks.pop(output_id, None)
            return None
        return lock

    def _reject(self, intent: RoutingIntent, reason: str, start: float) -> RoutingDecision:
        return self._record(intent, DecisionOutcome.REJECTED, reason, start, applied=False)

    def _record(
        self,
        intent: RoutingIntent,
        outcome: DecisionOutcome,
        reason: str,
        start: float,
        applied: bool,
    ) -> RoutingDecision:
        end = now_ms()
        decision = RoutingDecision(
            outcome=outcome,
            output_id=intent.target_output,
            input_id=intent.candidate_input if applied else self.matrix.current_input_for(
                intent.target_output
            ),
            producer=intent.producer,
            reason=reason,
            intent_id=intent.intent_id,
            age_ms=intent.age_ms(start),
            decision_ms=end - start,
        )
        self.audit.append(decision)
        self.decisions_emitted += 1
        logger.info(
            "routing %s output=%s input=%s producer=%s reason=%s age_ms=%.0f decision_ms=%.1f",
            outcome.value,
            decision.output_id,
            decision.input_id,
            decision.producer,
            reason,
            decision.age_ms,
            decision.decision_ms,
        )
        return decision

    def _control_decision(
        self, command: ControlCommand, outcome: DecisionOutcome, reason: str, start: float
    ) -> RoutingDecision:
        end = now_ms()
        decision = RoutingDecision(
            outcome=outcome,
            output_id=command.target_output,
            input_id=command.candidate_input,
            producer=f"operator:{command.operator}",
            reason=reason,
            intent_id=command.command_id,
            age_ms=now_ms() - command.created_at,
            decision_ms=end - start,
        )
        self.audit.append(decision)
        self.decisions_emitted += 1
        logger.info(
            "control %s output=%s input=%s operator=%s reason=%s",
            outcome.value,
            decision.output_id,
            decision.input_id,
            command.operator,
            reason,
        )
        return decision

    def audit_snapshot(self, limit: int = 50) -> list[dict]:
        items = list(self.audit)[-limit:]
        return [
            {
                "decision_id": decision.decision_id,
                "outcome": decision.outcome.value,
                "output_id": decision.output_id,
                "input_id": decision.input_id,
                "producer": decision.producer,
                "reason": decision.reason,
                "age_ms": decision.age_ms,
                "decision_ms": decision.decision_ms,
                "created_at": decision.created_at,
            }
            for decision in items
        ]
