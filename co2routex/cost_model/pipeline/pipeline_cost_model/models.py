"""Explicit units for direct costs and annual-flow affine coefficients."""
from __future__ import annotations
from dataclasses import dataclass, field
import math


@dataclass(frozen=True)
class RouteInput:
    from_id: str
    to_id: str
    distance_km: float
    average_route_resistance: float = 1.0  # Routing metadata only; never scales costs.
    scenario_id: str = ""
    annual_flow_t_per_year: float | None = None
    annual_flow_min_t_per_year: float | None = None
    annual_flow_max_t_per_year: float | None = None
    terrain: str | None = None
    source_row: int | None = None


@dataclass(frozen=True)
class DirectCost:
    annual_flow_t_per_year: float
    operating_flow_t_per_h: float
    massflow_kg_per_s: float
    terrain: str
    design: dict
    assumptions: dict
    energy: dict[str, float]
    costs: dict[str, dict[str, float]]


@dataclass(frozen=True)
class AffineCoefficient:
    intercept_eur_per_year: float
    slope_eur_per_t: float

    def value(self, annual_flow_t_per_year: float) -> float:
        return self.intercept_eur_per_year + self.slope_eur_per_t * annual_flow_t_per_year


@dataclass(frozen=True)
class Approximation:
    annual_flow_min_t_per_year: float
    annual_flow_max_t_per_year: float
    flow_range_basis: str
    fit_method: str
    coefficients: dict[str, dict[str, AffineCoefficient]]
    diagnostics: dict
    samples: list[tuple[str, DirectCost]]
    warnings: list[str] = field(default_factory=list)
    coefficient_method: str = "flow_range"

    def predict(self, annual_flow_t_per_year: float, *, basis="baseline", component="total", built=True) -> float:
        if basis not in self.coefficients:
            raise ValueError(f"Unknown cost basis {basis!r}; available: {', '.join(self.coefficients)}")
        if not built:
            if annual_flow_t_per_year != 0:
                raise ValueError("An unbuilt connection cannot carry flow")
            return 0.0
        if not self.annual_flow_min_t_per_year <= annual_flow_t_per_year <= self.annual_flow_max_t_per_year:
            raise ValueError("Prediction is outside the approximation's annual-flow range")
        return self.coefficients[basis][component].value(annual_flow_t_per_year)


@dataclass(frozen=True)
class FixedDesignApproximation:
    """Accounting decomposition of a single installed pipeline design.

    Off-reference costs assume constant electricity per tonne. This is not an
    off-design hydraulic calculation and does not resize the installed pipe.
    """
    reference_annual_flow_t_per_year: float
    coefficients: dict[str, dict[str, AffineCoefficient]]
    reference: DirectCost
    coefficient_method: str = "fixed_design"
    operating_assumption: str = "Fixed installed design and constant electricity per tonne; off-reference hydraulics are not evaluated"
    warnings: list[str] = field(default_factory=list)

    def predict(self, annual_flow_t_per_year: float, *, basis="baseline", component="total", built=True) -> float:
        if basis not in self.coefficients:
            raise ValueError(f"Unknown cost basis {basis!r}; available: {', '.join(self.coefficients)}")
        if not math.isfinite(annual_flow_t_per_year) or annual_flow_t_per_year < 0:
            raise ValueError("Annual flow must be finite and nonnegative")
        if not built:
            if annual_flow_t_per_year != 0:
                raise ValueError("An unbuilt connection cannot carry flow")
            return 0.0
        if annual_flow_t_per_year > self.reference_annual_flow_t_per_year:
            raise ValueError("Flow exceeds the reference design flow; size a new pipeline for this flow")
        return self.coefficients[basis][component].value(annual_flow_t_per_year)


@dataclass(frozen=True)
class RouteCostResult:
    route: RouteInput
    approximation: Approximation | FixedDesignApproximation | None = None
    benchmark: DirectCost | None = None
    error_message: str | None = None

    @property
    def cost_status(self) -> str:
        return "failed" if self.error_message is not None else "ok"
