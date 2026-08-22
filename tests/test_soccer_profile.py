from __future__ import annotations

import json
from pathlib import Path

from excalibur.matrix.soccer.config import SoccerProfile, load_soccer_profile


def test_soccer_profile_loads_and_matches_schema_required_events():
    profile = load_soccer_profile("configs/games/soccer.json")
    assert profile.sport == "soccer"
    assert profile.main_input == "input_01"
    assert set(profile.events) == {
        "foul",
        "free_kick",
        "penalty_kick",
        "corner_kick",
        "burst_attack",
    }
    assert profile.gating.ordinary_pass_not_event is True
    assert profile.manual_suspension.resume_action == "explicit_resume"


def test_soccer_event_examples_are_parseable():
    examples = json.loads(
        Path("configs/soccer.events.example.json").read_text(encoding="utf-8")
    )["examples"]
    assert examples[0]["kind"] == "heartbeat"
    assert {item.get("event_type") for item in examples if item["kind"] == "event"} >= {
        "foul",
        "free_kick",
    }


def test_profile_rejects_unknown_fields():
    payload = load_soccer_profile("configs/games/soccer.json").model_dump()
    payload["not_a_field"] = True
    try:
        SoccerProfile.model_validate(payload)
        assert False, "expected extra field to fail"
    except Exception as error:
        assert "not_a_field" in str(error)
