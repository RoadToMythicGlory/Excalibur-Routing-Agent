from __future__ import annotations

import copy
import logging
import threading
import time

from obs_auto_director.matrix.clock import advance_now_override, set_now_override
from obs_auto_director.matrix.gsi.simulator import clutch_round_sequence
from obs_auto_director.matrix.service import MatrixService

logger = logging.getLogger(__name__)


def run_clutch_demo(obs_dry_run: bool = True, step_ms: float = 250.0) -> MatrixService:
    """Feed the simulated clutch round through the full pipeline.

    Uses the deterministic clock override so latency and TTL math are stable.
    Returns the service so callers (CLI, tests) can inspect routes and audit.
    """

    set_now_override(1_700_000_000_000.0)
    service = MatrixService(obs_dry_run=obs_dry_run)
    service.connect_obs()

    for payload in clutch_round_sequence():
        # Align received_at with provider timestamp so state reads as fresh.
        provider_ts_ms = float(payload["provider"]["timestamp"]) * 1000.0
        set_now_override(provider_ts_ms)
        service.tick_frames()
        service.feed_gsi(payload)
        advance_now_override(step_ms)

    return service


def start_live_simulation(
    service: MatrixService, step_sec: float = 1.6
) -> threading.Event:
    """Feed the clutch round into a running service in real time, on a loop.

    Each loop uses a fresh provider timestamp and a new round number so the
    Match Intelligence Engine re-arms and re-emits stories. Returns a stop event
    the caller can set to end the feeder thread.
    """

    stop = threading.Event()

    def loop() -> None:
        round_number = 18
        while not stop.is_set():
            for payload in clutch_round_sequence():
                if stop.is_set():
                    return
                live = copy.deepcopy(payload)
                live["provider"]["timestamp"] = time.time()
                live["map"]["round"] = round_number
                service.tick_frames()
                service.feed_gsi(live)
                stop.wait(step_sec)
            round_number += 1

    thread = threading.Thread(target=loop, name="gsi-simulator", daemon=True)
    thread.start()
    logger.info("Live GSI simulation started (looping clutch round).")
    return stop


def print_summary(service: MatrixService) -> None:
    snapshot = service.state_snapshot()
    program = service.matrix.current_input_for(service.program_output)
    print(f"Program output {service.program_output} -> {program}")
    print(f"OBS scene: {service.obs.current_scene}")
    print("\nStory events:")
    for event in snapshot["intelligence"]:
        print(
            f"  {event['type']:<18} prio={event['priority']:>3} "
            f"conf={event['confidence']:.2f} inputs={event['recommended_inputs']}"
        )
    print("\nRouting audit (last 10):")
    for decision in snapshot["audit"][-10:]:
        print(
            f"  {decision['outcome']:<10} {decision['output_id']} <- {decision['input_id']} "
            f"by {decision['producer']} ({decision['reason']}) "
            f"age={decision['age_ms']:.0f}ms"
        )
