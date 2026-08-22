"""Typed data exchanged by the pipeline cost model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteInput:
    from_id: str
    to_id: str
    distance_km: float
    average_route_resistance: float


@dataclass(frozen=True)
class EndpointCost:
    capacity_t_per_h: float
    capex_pipeline_eur: float
    capex_compression_eur: float
    capex_total_eur: float
    selected_inner_diameter_m: float | None
    selected_outer_diameter_m: float | None
    steel_grade: str | None
    number_of_pumps: int | None
    specific_compression_energy_mwh_per_t: float | None


@dataclass(frozen=True)
class RouteCostResult:
    route: RouteInput
    minimum: EndpointCost
    maximum: EndpointCost
    capex_baseline_slope_eur_per_t_per_h: float
    capex_baseline_intercept_eur: float
    capex_spatial_slope_eur_per_t_per_h: float
    capex_spatial_intercept_eur: float

    def baseline_capex(self, capacity_t_per_h: float) -> float:
        return (
            self.capex_baseline_slope_eur_per_t_per_h * capacity_t_per_h
            + self.capex_baseline_intercept_eur
        )

    def spatial_capex(self, capacity_t_per_h: float) -> float:
        return (
            self.capex_spatial_slope_eur_per_t_per_h * capacity_t_per_h
            + self.capex_spatial_intercept_eur
        )
