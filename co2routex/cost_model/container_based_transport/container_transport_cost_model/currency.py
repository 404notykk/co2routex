"""Reproducible EUR 2021 -> EUR 2024 conversion, matching AdOpT-NET0 0.1.10.

Its Eurostat STS_INPP_M snapshot is dated 2025-01-21 and ends at November 2024.
The incomplete target-year coverage is deliberately exposed in every output.
Only monetary values are converted; no exchange rate or discounting is applied.
"""
from __future__ import annotations

import math
from statistics import fmean


PPI_2021 = (93.3, 93.9, 94.9, 95.8, 96.7, 97.9, 99.8, 100.7, 102.6, 106.4, 107.9, 110.0)
PPI_2024 = (121.7, 120.9, 120.6, 120.1, 119.9, 120.4, 120.9, 121.1, 120.6, 121.0, 122.5)
DEFAULT_FACTOR = fmean(PPI_2024) / fmean(PPI_2021)
DATA_SOURCE = (
    "https://github.com/UU-ER/AdOpT-NET0/blob/v0.1.10/"
    "adopt_net0/database/data/producer_price_index_euro.csv"
)
DEFAULT_BASIS = "AdOpT-NET0 0.1.10 Eurostat PPI snapshot: 2021 Jan-Dec; 2024 Jan-Nov (11 months)"


def conversion_metadata(config):
    """Describe the exact factor used, including target-year coverage."""
    custom = config.eur2021_to_eur2024_factor is not None
    metadata = {
        "source_currency": "EUR", "source_price_year": 2021, "target_price_year": 2024,
        "eur2021_to_eur2024_factor": config.conversion_factor,
        "conversion_method": "user_factor" if custom else "adopt_net0_eurostat_ppi_mean_ratio",
        "conversion_basis": config.eur2024_factor_source if custom else DEFAULT_BASIS,
    }
    if not custom:
        metadata.update(
            conversion_data_source=DATA_SOURCE, conversion_snapshot_date="2025-01-21",
            ppi_2021_mean=fmean(PPI_2021), ppi_2024_mean=fmean(PPI_2024),
            ppi_2021_months=12, ppi_2024_months=11,
            ppi_2024_coverage="2024-01 through 2024-11; December is absent",
            ppi_series="Euro area 20; industry excluding construction, sewerage, waste management and remediation; producer prices; unadjusted; index 2021=100",
        )
    return metadata


def to_eur2024(value, factor):
    """Convert a monetary value; keep missing values missing and reject overflow."""
    if value is None:
        return None
    converted = value * factor
    if not math.isfinite(converted):
        raise ValueError("Calculated EUR 2024 cost is nonfinite")
    return converted


def monetary_values_2024(result, factor):
    """F, v, annual cost, average cost, then UC, in the output column order."""
    c = result.coefficient
    if c is None:
        return [None] * 5
    return [to_eur2024(value, factor) for value in (
        0.0, c.gamma2_eur_per_t, result.annual_cost_eur_per_year,
        result.average_cost_eur_per_t, c.unit_cost_eur_per_t_km,
    )]
