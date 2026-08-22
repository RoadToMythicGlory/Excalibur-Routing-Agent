from __future__ import annotations

from excalibur.matrix.soccer.config import SoccerProfile
from excalibur.matrix.soccer.domain import CameraAssignment, CameraRole


class SoccerCameraMap:
    """Pins Input 1 as the lead camera and tracks inferred roles for the rest."""

    def __init__(self, profile: SoccerProfile) -> None:
        self.profile = profile
        self.assignments: dict[str, CameraAssignment] = {}
        self.pin_main()

    def pin_main(self) -> CameraAssignment:
        assignment = CameraAssignment(
            input_id=self.profile.main_input,
            role=self.profile.pinned_main_role(),
            confidence=1.0,
            source="pinned",
        )
        self.assignments[self.profile.main_input] = assignment
        return assignment

    def update(self, assignment: CameraAssignment) -> CameraAssignment:
        if assignment.input_id == self.profile.main_input:
            return self.pin_main()
        current = self.assignments.get(assignment.input_id)
        if (
            current is not None
            and current.confidence >= assignment.confidence
            and current.role != CameraRole.UNKNOWN.value
            and assignment.role == CameraRole.UNKNOWN.value
        ):
            return current
        self.assignments[assignment.input_id] = assignment
        return assignment

    def role_for(self, input_id: str) -> CameraAssignment:
        if input_id == self.profile.main_input:
            return self.pin_main()
        return self.assignments.get(
            input_id,
            CameraAssignment(
                input_id=input_id,
                role=CameraRole.UNKNOWN.value,
                confidence=0.0,
                source="unknown",
            ),
        )

    def inputs_for_role(self, role: str) -> list[str]:
        return [
            input_id
            for input_id, assignment in self.assignments.items()
            if assignment.role == role
        ]

    def snapshot(self) -> dict[str, dict]:
        return {
            input_id: {
                "role": assignment.role,
                "confidence": assignment.confidence,
                "source": assignment.source,
                "score": assignment.score,
            }
            for input_id, assignment in self.assignments.items()
        }
