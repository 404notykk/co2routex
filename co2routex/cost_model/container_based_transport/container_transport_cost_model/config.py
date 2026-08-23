"""YAML configuration and validation."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from .models import ModeParameters


@dataclass(frozen=True)
class TransportCostConfig:
    workbook_path: Path
    output_dir: Path = Path("outputs")
    combined_output_workbook: str = "node_metrics_transport_costed.xlsx"
    truck_output_workbook: str = "node_metrics_truck_costed.xlsx"
    railway_output_workbook: str = "node_metrics_railway_costed.xlsx"
    truck_sheet: str = "truck"
    railway_sheet: str = "railway"

    truck_fixed_eur_per_t: float = 5.58
    truck_distance_rate_eur_per_t_km: float = 0.15
    railway_fixed_eur_per_t: float = 28.9
    railway_distance_rate_eur_per_t_km: float = 0.07

    currency: str = "EUR"
    cost_reference_year: int | None = None
    source_note: str = "Container-based transport regressions supplied with CO2RouteX"

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TransportCostConfig":
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
                values[key] = candidate if candidate.is_absolute() else base / candidate

        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:
        if not self.workbook_path.is_file():
            raise FileNotFoundError(f"Input workbook not found: {self.workbook_path}")
        if self.workbook_path.suffix.lower() != ".xlsx":
            raise ValueError("workbook_path must point to an .xlsx file")
        if self.currency != "EUR":
            raise ValueError("The supplied regressions are denominated in EUR")
        for name in (
            "combined_output_workbook",
            "truck_output_workbook",
            "railway_output_workbook",
        ):
            value = getattr(self, name)
            if not value.lower().endswith(".xlsx"):
                raise ValueError(f"{name} must end with .xlsx")
        for name in (
            "truck_fixed_eur_per_t",
            "truck_distance_rate_eur_per_t_km",
            "railway_fixed_eur_per_t",
            "railway_distance_rate_eur_per_t_km",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")

    def parameters(self, mode: str) -> ModeParameters:
        if mode == "truck":
            return ModeParameters(
                mode="truck",
                fixed_eur_per_t=self.truck_fixed_eur_per_t,
                distance_rate_eur_per_t_km=self.truck_distance_rate_eur_per_t_km,
            )
        if mode == "railway":
            return ModeParameters(
                mode="railway",
                fixed_eur_per_t=self.railway_fixed_eur_per_t,
                distance_rate_eur_per_t_km=self.railway_distance_rate_eur_per_t_km,
            )
        raise ValueError(f"Unsupported mode: {mode!r}")

    def source_sheet(self, mode: str) -> str:
        return self.truck_sheet if mode == "truck" else self.railway_sheet

    def output_name(self, modes: tuple[str, ...]) -> str:
        if modes == ("truck",):
            return self.truck_output_workbook
        if modes == ("railway",):
            return self.railway_output_workbook
        return self.combined_output_workbook

