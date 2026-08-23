"""Distance-based cost equations."""

from __future__ import annotations

import math

from .models import ModeParameters, PairCoefficient


def unit_cost(distance_km: float, parameters: ModeParameters) -> float:
    """Return UC(d) in EUR/(t*km)."""
    distance = _positive_distance(distance_km)
    return parameters.fixed_eur_per_t / distance + parameters.distance_rate_eur_per_t_km


def calculate_gamma2(distance_km: float, parameters: ModeParameters) -> float:
    """Return pair-specific gamma2 = UC(d) * d in EUR/t."""
    distance = _positive_distance(distance_km)
    return parameters.fixed_eur_per_t + parameters.distance_rate_eur_per_t_km * distance


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
        distance = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("distance_km must be numeric") from exc
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("distance_km must be finite and greater than zero")
    return distance

