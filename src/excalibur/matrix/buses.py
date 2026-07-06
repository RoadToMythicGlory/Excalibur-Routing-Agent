from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

from excalibur.matrix.clock import now_ms
from excalibur.matrix.domain import (
    ControlCommand,
    GameStateEvent,
    MatchIntelligenceEvent,
    RoutingIntent,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

Subscriber = Callable[[T], None]


@dataclass
class _BusStats:
    published: int = 0
    delivered: int = 0
    errors: int = 0


class InProcessBus(Generic[T]):
    """Minimal synchronous pub/sub.

    The surface is intentionally tiny (`publish` / `subscribe`) so it can be
    replaced by Redis Streams, NATS, or Kafka later without changing producers
    or consumers. A bounded history buffer feeds the control-plane UI.
    """

    def __init__(self, name: str, history: int = 200) -> None:
        self.name = name
        self._subscribers: list[Subscriber[T]] = []
        self._history: deque[T] = deque(maxlen=history)
        self.stats = _BusStats()

    def subscribe(self, subscriber: Subscriber[T]) -> Callable[[], None]:
        self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

        return unsubscribe

    def publish(self, message: T) -> None:
        self.stats.published += 1
        self._history.append(message)
        for subscriber in list(self._subscribers):
            try:
                subscriber(message)
                self.stats.delivered += 1
            except Exception:  # pragma: no cover - defensive, must not break bus
                self.stats.errors += 1
                logger.exception("Subscriber on bus %s failed", self.name)

    def recent(self, limit: int = 50) -> list[T]:
        items = list(self._history)
        return items[-limit:]


@dataclass
class MatrixBuses:
    """The five logical buses of the architecture.

    Producers and the routing engine only ever touch these; nobody mutates
    matrix route state directly.
    """

    decision: InProcessBus[RoutingIntent] = field(
        default_factory=lambda: InProcessBus("decision")
    )
    control: InProcessBus[ControlCommand] = field(
        default_factory=lambda: InProcessBus("control")
    )
    rule: InProcessBus[RoutingIntent] = field(default_factory=lambda: InProcessBus("rule"))
    game_state: InProcessBus[GameStateEvent] = field(
        default_factory=lambda: InProcessBus("game_state")
    )
    intelligence: InProcessBus[MatchIntelligenceEvent] = field(
        default_factory=lambda: InProcessBus("intelligence")
    )

    def stats_snapshot(self) -> dict[str, dict[str, int]]:
        return {
            bus.name: {
                "published": bus.stats.published,
                "delivered": bus.stats.delivered,
                "errors": bus.stats.errors,
            }
            for bus in (
                self.decision,
                self.control,
                self.rule,
                self.game_state,
                self.intelligence,
            )
        }
