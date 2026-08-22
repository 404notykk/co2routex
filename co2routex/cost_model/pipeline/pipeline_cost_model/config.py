"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PipelineCostConfig:
    """Settings used for every route evaluated in one run."""

    workbook_path: Path
    output_dir: Path = Path("outputs")
    output_workbook_name: str = "node_metrics_costed.xlsx"
    detailed_workbook_name: str = "pipeline_cost_details.xlsx"
    route_metrics_sheet: str = "pipeline_route_metrics"

    capacity_min_t_per_h: float = 18.0
    capacity_max_t_per_h: float = 4050.0
    capacity_range_basis: str = "temporary_DN_square_law_proxy"

    terrain: str = "Onshore"
    timeframe: str = "mid-term"
    electricity_price_eur_per_mwh: float = 60.0
    operating_hours_per_year: float = 8000.0
    p_inlet_bar: float = 10.0
    p_outlet_bar: float = 70.0
    discount_rate: float = 0.10
    currency: str = "EUR"
    cost_reference_year: int = 2021

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PipelineCostConfig":
        config_path = Path(path).expanduser().resolve()
        with config_path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream) or {}

        if not isinstance(raw, dict):
            raise ValueError("The YAML configuration must contain a mapping")

        allowed = {field.name for field in fields(cls)}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(f"Unknown configuration keys: {', '.join(unknown)}")

        base = config_path.parent
        values: dict[str, Any] = dict(raw)
        for key in ("workbook_path", "output_dir"):
            if key in values:
                candidate = Path(values[key]).expanduser()
                values[key] = candidate if candidate.is_absolute() else (base / candidate)

        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:
        if not self.workbook_path.is_file():
            raise FileNotFoundError(f"Input workbook not found: {self.workbook_path}")
        if self.workbook_path.suffix.lower() != ".xlsx":
            raise ValueError("workbook_path must point to an .xlsx file")
        if self.capacity_min_t_per_h <= 0:
            raise ValueError("capacity_min_t_per_h must be greater than zero")
        if self.capacity_max_t_per_h <= self.capacity_min_t_per_h:
            raise ValueError("capacity_max_t_per_h must exceed capacity_min_t_per_h")
        if self.terrain not in {"Onshore", "Offshore"}:
            raise ValueError("terrain must be 'Onshore' or 'Offshore'")
        if self.timeframe not in {"near-term", "mid-term", "long-term"}:
            raise ValueError("Unsupported timeframe")
        if self.operating_hours_per_year <= 0:
            raise ValueError("operating_hours_per_year must be greater than zero")
        if self.discount_rate <= 0:
            raise ValueError("discount_rate must be greater than zero")
        if self.cost_reference_year != 2021 or self.currency != "EUR":
            raise ValueError("This version returns costs only in EUR_2021")
