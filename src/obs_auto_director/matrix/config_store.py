from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

INPUT_FIELDS = (
    "player_name",
    "team",
    "role",
    "obs_scene",
    "srt_mode",
    "srt_host",
    "srt_port",
    "srt_latency",
)


class MatrixConfigStore:
    """Operator configuration persisted to a JSON file.

    Holds per-input metadata (player name, team, role, OBS scene) and the program
    output selection. The matrix ships with no real identities; everything here
    is set by the operator and survives restarts.
    """

    def __init__(self, path: str = "configs/matrix.runtime.json") -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data: dict = {
            "program_output": "output_01",
            "program_publish_url": None,
            "inputs": {},
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data["program_output"] = loaded.get("program_output", "output_01")
                self.data["program_publish_url"] = loaded.get("program_publish_url")
                self.data["inputs"] = loaded.get("inputs", {}) or {}
                logger.info("Loaded matrix config from %s", self.path)
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("Could not load matrix config %s: %s", self.path, error)

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def set_input(self, input_id: str, fields: dict) -> dict:
        clean = {key: value for key, value in fields.items() if key in INPUT_FIELDS}
        current = self.data["inputs"].get(input_id, {})
        current.update(clean)
        self.data["inputs"][input_id] = current
        self.save()
        return current

    def input_config(self, input_id: str) -> dict:
        return self.data["inputs"].get(input_id, {})

    def all_inputs(self) -> dict:
        return dict(self.data["inputs"])

    @property
    def program_output(self) -> str:
        return self.data.get("program_output", "output_01")

    def set_program_output(self, output_id: str) -> None:
        self.data["program_output"] = output_id
        self.save()

    @property
    def program_publish_url(self) -> str | None:
        return self.data.get("program_publish_url")

    def set_program_publish_url(self, publish_url: str | None) -> None:
        self.data["program_publish_url"] = publish_url
        self.save()
