"""Data structures used by the cost model."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class ModeParameters:
    """Regression parameters for UC(d) = fixed / d + distance_rate."""

    mode: str
    fixed_eur_per_t: float
    distance_rate_eur_per_t_km: float

    def validate(self) -> None:
        for name in ("fixed_eur_per_t", "distance_rate_eur_per_t_km"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{self.mode}/{name} must be finite and non-negative")


@dataclass(frozen=True)
class PairCoefficient:
    """A route-specific coefficient for one directed node pair."""

    mode: str
    from_id: str
    to_id: str
    distance_km: float
    unit_cost_eur_per_t_km: float
    gamma1_eur: float
    gamma2_eur_per_t: float
    gamma3_eur_per_km: float
    gamma4_eur_per_t_km: float


@dataclass(frozen=True)
class RouteInput:
    mode: str
    from_id: Any
    to_id: Any
    distance_km: Any
    annual_flow_t_per_year: Any = None
    source_row: int = 1  # 1-based data row, not a saved pandas index.
    source_sheet: str | None = None
    source_cell: str | None = None


@dataclass(frozen=True)
class RouteCostResult:
    route: RouteInput
    coefficient: PairCoefficient | None = None
    reference_flow_t_per_year: float | None = None
    annual_cost_eur_per_year: float | None = None
    average_cost_eur_per_t: float | None = None
    cost_error: str | None = None
    cost_note: str | None = None

    @property
    def cost_status(self) -> str:
        return "failed" if self.cost_error is not None else "ok"


@dataclass(frozen=True)
class InputData:
    routes: list[RouteInput]
    layout: str
    source_sheet: str | None = None
    node_ids_by_mode: dict[str, list[str]] | None = None
