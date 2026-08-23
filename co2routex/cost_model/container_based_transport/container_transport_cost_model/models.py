"""Data structures used by the cost model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModeParameters:
    """Regression parameters for UC(d) = fixed / d + distance_rate."""

    mode: str
    fixed_eur_per_t: float
    distance_rate_eur_per_t_km: float


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

