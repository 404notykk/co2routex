"""Distance-based cost equations."""

from __future__ import annotations

import math

from .models import ModeParameters, PairCoefficient, RouteInput, RouteCostResult
from .currency import to_eur2024


def unit_cost(distance_km: float, parameters: ModeParameters) -> float:
    """Return UC(d) in EUR/(t*km)."""
    distance = _positive_distance(distance_km)
    parameters.validate()
    value = parameters.fixed_eur_per_t / distance + parameters.distance_rate_eur_per_t_km
    if not math.isfinite(value):
        raise ValueError("Calculated unit cost is nonfinite")
    return value


def calculate_gamma2(distance_km: float, parameters: ModeParameters) -> float:
    """Return pair-specific gamma2 = UC(d) * d in EUR/t."""
    distance = _positive_distance(distance_km)
    parameters.validate()
    value = parameters.fixed_eur_per_t + parameters.distance_rate_eur_per_t_km * distance
    if not math.isfinite(value):
        raise ValueError("Calculated flow coefficient is nonfinite")
    return value


def calculate_pair_coefficient(
    mode: str,
    from_id: str,
    to_id: str,
    distance_km: float,
    parameters: ModeParameters,
) -> PairCoefficient:
    distance = _positive_distance(distance_km)
    return PairCoefficient(
        mode=mode,
        from_id=from_id,
        to_id=to_id,
        distance_km=distance,
        unit_cost_eur_per_t_km=unit_cost(distance, parameters),
        gamma1_eur=0.0,
        gamma2_eur_per_t=calculate_gamma2(distance, parameters),
        gamma3_eur_per_km=0.0,
        gamma4_eur_per_t_km=0.0,
    )


def _positive_distance(value: float) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError()
        distance = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("distance_km must be numeric") from exc
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("distance_km must be finite and greater than zero")
    return distance


def calculate_route(route: RouteInput, config) -> RouteCostResult:
    """Evaluate F=0, v=a+b*d, C=v*Q; no capital annualisation or flow fit."""
    from .config import finite_nonnegative
    from .inputs import clean_id, missing

    coefficient = calculate_pair_coefficient(
        route.mode, clean_id(route.from_id), clean_id(route.to_id),
        route.distance_km, config.parameters(route.mode),
    )
    # A route that cannot be represented in either price year must fail per route.
    factor = config.conversion_factor
    to_eur2024(coefficient.gamma2_eur_per_t, factor)
    to_eur2024(coefficient.unit_cost_eur_per_t_km, factor)
    value = route.annual_flow_t_per_year
    if missing(value):
        value = config.annual_flow_t_per_year
    if missing(value):
        if config.require_annual_flow:
            raise ValueError("Annual transported CO2 is required; supply row flow or annual_flow_t_per_year in YAML")
        return RouteCostResult(route, coefficient, cost_note="Coefficients only: annual transported flow was not supplied")
    annual_flow = finite_nonnegative(value, "annual_flow_t_per_year")
    annual_cost = coefficient.gamma2_eur_per_t * annual_flow
    if not math.isfinite(annual_cost):
        raise ValueError("Calculated annual cost is nonfinite")
    to_eur2024(annual_cost, factor)
    average = coefficient.gamma2_eur_per_t if annual_flow > 0 else None
    note = "Zero reference flow: annual cost is zero; average cost is undefined" if annual_flow == 0 else None
    return RouteCostResult(route, coefficient, annual_flow, annual_cost, average, cost_note=note)
