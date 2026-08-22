"""Generate candidate connections for the CO2 raster-routing stage."""

from .generator import generate_candidates
from .models import CandidateConnection, Node, Settings
from .pipeline import RunResult, run

__all__ = [
    "CandidateConnection",
    "Node",
    "RunResult",
    "Settings",
    "generate_candidates",
    "run",
]

__version__ = "0.3.0"
