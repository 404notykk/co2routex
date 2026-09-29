"""Reproducible EUR 2021 -> EUR 2024 export conversion, matching AdOpT-NET0.

The monthly observations below are from AdOpT-NET0 0.1.10's Eurostat PPI
snapshot, dated 2025-01-21. Its 2024 data end in November. This same snapshot
is used by the companion container transport package. Engineering calculations
remain in EUR 2021; this module only converts exported monetary values.
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
    """Describe the resolved factor and assumptions, including year coverage."""
    custom = config.eur2021_to_eur2024_factor is not None
    metadata = {
        "source_currency": "EUR", "source_price_year": 2021, "target_price_year": 2024,
        "eur2021_to_eur2024_factor": config.conversion_factor,
        "conversion_method": "user_factor" if custom else "adopt_net0_eurostat_ppi_mean_ratio",
        "conversion_basis": config.eur2024_factor_source if custom else DEFAULT_BASIS,
        "conversion_scope": "All monetary results: upfront and annualized CAPEX, fixed OPEX, electricity OPEX, F, v, annual totals and average costs. Physical design is unchanged.",
        "electricity_price_basis": "Configured electricity price is assumed EUR 2021/MWh and its costs receive the same PPI factor. The default 60 is a scenario assumption, not an observed annual market price.",
    }
    if not custom:
        metadata.update(
            conversion_data_source=DATA_SOURCE, conversion_snapshot_date="2025-01-21",
            ppi_2021_mean=fmean(PPI_2021), ppi_2024_mean=fmean(PPI_2024),
            ppi_2021_months=12, ppi_2024_months=11,
            ppi_2024_coverage="2024-01 through 2024-11; December is absent",
            ppi_series="sts_inpp_m: geo=EA20, nace_r2=B-E36, indic_bt=PRC_PRR, s_adj=NSA, unit=I21, freq=M",
        )
    return metadata


def to_eur2024(value, factor=DEFAULT_FACTOR):
    """Keep missing values missing, and reject nonfinite converted costs."""
    if value is None:
        return None
    converted = value * factor
    if not math.isfinite(converted):
        raise ValueError("Calculated EUR 2024 cost is nonfinite")
    return converted


def add_eur2024_columns(row, factor=DEFAULT_FACTOR):
    """Return a copy with EUR 2024 counterparts of named EUR 2021 fields."""
    extra = {
        name.replace("_eur", "_eur2024", 1): to_eur2024(value, factor)
        for name, value in row.items()
        if name.endswith(("_eur", "_eur_per_year", "_eur_per_t", "_eur_per_mwh"))
    }
    return {**row, **extra}
