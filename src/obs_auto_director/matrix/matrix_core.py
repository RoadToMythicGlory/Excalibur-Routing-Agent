from __future__ import annotations

from obs_auto_director.matrix.clock import now_ms
from obs_auto_director.matrix.domain import (
    MIN_SLOTS,
    InputSlot,
    OutputKind,
    OutputSlot,
    Route,
    SlotState,
    input_slot_id,
    output_slot_id,
)


class MatrixCore:
    """The truth layer for what exists and what can be routed.

    Always exposes at least 11 input slots and 11 output slots. Only the routing
    engine should call `apply_route`; everything else reads state.
    """

    def __init__(self, inputs: int = MIN_SLOTS, outputs: int = MIN_SLOTS) -> None:
        slot_count = max(MIN_SLOTS, inputs)
        output_count = max(MIN_SLOTS, outputs)
        self.inputs: dict[str, InputSlot] = {}
        self.outputs: dict[str, OutputSlot] = {}
        self.routes: dict[str, Route] = {}

        for index in range(1, slot_count + 1):
            slot_id = input_slot_id(index)
            self.inputs[slot_id] = InputSlot(id=slot_id, label=f"Input {index:02d}")

        for index in range(1, output_count + 1):
            slot_id = output_slot_id(index)
            self.outputs[slot_id] = OutputSlot(id=slot_id, label=f"Output {index:02d}")

    def configure_input(self, slot_id: str, **fields) -> InputSlot:
        slot = self.inputs[slot_id]
        for key, value in fields.items():
            setattr(slot, key, value)
        return slot

    def configure_output(self, slot_id: str, **fields) -> OutputSlot:
        slot = self.outputs[slot_id]
        for key, value in fields.items():
            setattr(slot, key, value)
        return slot

    def input_available(self, slot_id: str) -> bool:
        slot = self.inputs.get(slot_id)
        return slot is not None and slot.has_signal

    def apply_route(self, output_id: str, input_id: str | None, set_by: str, reason: str) -> Route:
        if output_id not in self.outputs:
            raise KeyError(f"Unknown output slot: {output_id}")
        if input_id is not None and input_id not in self.inputs:
            raise KeyError(f"Unknown input slot: {input_id}")
        route = Route(output_id=output_id, input_id=input_id, set_by=set_by, reason=reason)
        self.routes[output_id] = route
        return route

    def current_input_for(self, output_id: str) -> str | None:
        route = self.routes.get(output_id)
        return route.input_id if route else None

    def mark_input_frame(self, slot_id: str, when: float | None = None) -> None:
        slot = self.inputs.get(slot_id)
        if slot is not None:
            slot.last_frame_at = when if when is not None else now_ms()

    def snapshot(self) -> dict:
        return {
            "inputs": [
                {
                    "id": slot.id,
                    "label": slot.label,
                    "state": slot.state.value,
                    "protocol": slot.protocol,
                    "player_name": slot.player_name,
                    "team": slot.team,
                    "role": slot.role,
                    "last_frame_at": slot.last_frame_at,
                }
                for slot in self.inputs.values()
            ],
            "outputs": [
                {
                    "id": slot.id,
                    "label": slot.label,
                    "kind": slot.kind.value,
                    "state": slot.state.value,
                    "obs_scene": slot.obs_scene,
                    "uri": slot.uri,
                    "input_id": self.current_input_for(slot.id),
                }
                for slot in self.outputs.values()
            ],
            "routes": [
                {
                    "output_id": route.output_id,
                    "input_id": route.input_id,
                    "set_by": route.set_by,
                    "reason": route.reason,
                    "set_at": route.set_at,
                }
                for route in self.routes.values()
            ],
        }
