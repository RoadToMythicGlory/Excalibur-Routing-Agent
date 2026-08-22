"""Soccer director: structured events, CV fallback, and shot planning."""

from excalibur.matrix.soccer.config import (
    SoccerProfile,
    load_soccer_profile,
)
from excalibur.matrix.soccer.domain import (
    CameraRole,
    DirectorMode,
    SoccerEventType,
    SoccerPhase,
    SourceAuthority,
)

__all__ = [
    "CameraRole",
    "DirectorMode",
    "SoccerEventType",
    "SoccerPhase",
    "SoccerProfile",
    "SourceAuthority",
    "load_soccer_profile",
]
