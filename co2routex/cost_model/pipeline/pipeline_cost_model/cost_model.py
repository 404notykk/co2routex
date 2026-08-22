"""Calculate route-specific baseline and spatial pipeline CAPEX coefficients."""

from __future__ import annotations

import contextlib
import io
import math

from .config import PipelineCostConfig
from .models import EndpointCost, RouteCostResult, RouteInput
from .oeuvray import CO2Chain_Oeuvray


class PipelineCostModel:
    """Thin CO2RouteX interface around the supplied Oeuvray implementation."""

    def __init__(self, config: PipelineCostConfig):
        config.validate()
        self.config = config

    def calculate_route(self, route: RouteInput) -> RouteCostResult:
        minimum = self._calculate_endpoint(route.distance_km, self.config.capacity_min_t_per_h)
        maximum = self._calculate_endpoint(route.distance_km, self.config.capacity_max_t_per_h)

        capacity_difference = maximum.capacity_t_per_h - minimum.capacity_t_per_h
        slope = (maximum.capex_total_eur - minimum.capex_total_eur) / capacity_difference
        intercept = minimum.capex_total_eur - slope * minimum.capacity_t_per_h
        resistance = route.average_route_resistance

        return RouteCostResult(
            route=route,
            minimum=minimum,
            maximum=maximum,
            capex_baseline_slope_eur_per_t_per_h=slope,
            capex_baseline_intercept_eur=intercept,
            capex_spatial_slope_eur_per_t_per_h=resistance * slope,
            capex_spatial_intercept_eur=resistance * intercept,
        )

    def _calculate_endpoint(self, length_km: float, capacity_t_per_h: float) -> EndpointCost:
        options = {
            "length_km": length_km,
            "timeframe": self.config.timeframe,
            "massflow_kg_per_s": capacity_t_per_h / 3.6,
            "terrain": self.config.terrain,
            "electricity_price_eur_per_mw": self.config.electricity_price_eur_per_mwh,
            "operating_hours_per_a": self.config.operating_hours_per_year,
            "p_inlet_bar": self.config.p_inlet_bar,
            "p_outlet_bar": self.config.p_outlet_bar,
            "discount_rate": self.config.discount_rate,
        }

        # The adopted source model prints every engineering candidate. Keep the
        # CO2RouteX CLI concise while retaining exceptions and returned data.
        source_model = CO2Chain_Oeuvray()
        with contextlib.redirect_stdout(io.StringIO()):
            result = source_model.calculate_cost(options)

        pipe_capex = float(result["cost_pipeline"]["unit_capex"])
        compression_capex = float(result["cost_compression"]["unit_capex"])
        configuration = result.get("configuration", {})

        # Adopt-Net0 annualises the pipeline component to the compressor
        # lifetime before forming its CAPEX coefficient. Preserve that adopted
        # convention here so the coefficients remain compatible.
        universal = source_model.universal_data
        correction = _capital_recovery_factor(
            self.config.discount_rate, universal["z_pipe"]
        ) / _capital_recovery_factor(
            self.config.discount_rate, universal["z_pumpcomp"]
        )
        corrected_pipe_capex = pipe_capex * correction
        total = corrected_pipe_capex + compression_capex

        return EndpointCost(
            capacity_t_per_h=capacity_t_per_h,
            capex_pipeline_eur=corrected_pipe_capex,
            capex_compression_eur=compression_capex,
            capex_total_eur=total,
            selected_inner_diameter_m=_optional_float(configuration.get("id_nps_m")),
            selected_outer_diameter_m=_optional_float(configuration.get("od_nps_m")),
            steel_grade=_optional_string(configuration.get("steel_grade")),
            number_of_pumps=_optional_int(configuration.get("n_pumps")),
            specific_compression_energy_mwh_per_t=_optional_float(
                result.get("energy_requirements", {}).get("specific_compression_energy")
            ),
        )


def _capital_recovery_factor(rate: float, years: float) -> float:
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


def _optional_float(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _optional_int(value):
    if value is None:
        return None
    return int(value)


def _optional_string(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None
