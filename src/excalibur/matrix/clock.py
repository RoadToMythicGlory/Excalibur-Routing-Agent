from __future__ import annotations

import time

"""Single source of time for the matrix.

All ages and latency math use wall-clock epoch milliseconds so that timestamps
remain comparable across the GSI receiver, buses, agents, and the routing
engine. A module-level override hook keeps tests deterministic.
"""

_now_override: float | None = None


def now_ms() -> float:
    if _now_override is not None:
        return _now_override
    return time.time() * 1000.0


def set_now_override(value_ms: float | None) -> None:
    global _now_override
    _now_override = value_ms


def advance_now_override(delta_ms: float) -> float:
    global _now_override
    if _now_override is None:
        _now_override = time.time() * 1000.0
    _now_override += delta_ms
    return _now_override
