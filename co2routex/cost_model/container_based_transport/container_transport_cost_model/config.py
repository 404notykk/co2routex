"""Configuration for distance regressions and annual route costing."""
from __future__ import annotations
from dataclasses import dataclass, fields
import math
from pathlib import Path
import yaml
from .models import ModeParameters
from .currency import DEFAULT_FACTOR


@dataclass(frozen=True)
class TransportCostConfig:
    # workbook_path remains an alias for old matrix configurations.
    workbook_path: Path | None = None
    input_path: Path | None = None
    input_layout: str = "auto"
    route_metrics_sheet: str = "transport_route_metrics"
    from_id_column: str = "from_id"
    to_id_column: str = "to_id"
    distance_column: str = "distance_km"
    annual_flow_column: str = "annual_flow_t_per_year"
    mode_column: str | None = "mode"
    status_column: str | None = "status"
    accepted_statuses: tuple[str, ...] = ("ok",)
    default_transport_mode: str | None = None
    annual_flow_t_per_year: float | None = None
    require_annual_flow: bool = False
    input_row: int | None = None

    output_dir: Path = Path("outputs")
    output_workbook_name: str | None = None
    write_xlsx: bool = True
    write_costed_input: bool = True
    combined_output_workbook: str = "node_metrics_transport_costed.xlsx"
    truck_output_workbook: str = "node_metrics_truck_costed.xlsx"
    railway_output_workbook: str = "node_metrics_railway_costed.xlsx"
    truck_sheet: str = "truck"
    railway_sheet: str = "railway"
    truck_annual_flow_sheet: str | None = None
    railway_annual_flow_sheet: str | None = None

    truck_fixed_eur_per_t: float = 5.58
    truck_distance_rate_eur_per_t_km: float = 0.15
    railway_fixed_eur_per_t: float = 28.9
    railway_distance_rate_eur_per_t_km: float = 0.07
    currency: str = "EUR"
    cost_reference_year: int = 2021
    eur2021_to_eur2024_factor: float | None = None
    eur2024_factor_source: str | None = None
    source_note: str = (
        "Oeuvray et al. (2024), Multi-criteria assessment of inland and offshore "
        "carbon dioxide transport options, doi:10.1016/j.jclepro.2024.140781; "
        "container truck/train UC curves supplied by the user."
    )

    @property
    def source_path(self) -> Path:
        source = self.input_path if self.input_path is not None else self.workbook_path
        if source is None:
            raise ValueError("Set input_path (or legacy workbook_path)")
        return Path(source)

    @classmethod
    def from_yaml(cls, path: str | Path, *, require_input: bool = True) -> "TransportCostConfig":
        config_path = Path(path).expanduser().resolve()
        with config_path.open(encoding="utf-8") as stream:
            raw = yaml.safe_load(stream) or {}
        if not isinstance(raw, dict):
            raise ValueError("The YAML configuration must contain a mapping")
        unknown = sorted(set(raw) - {f.name for f in fields(cls)})
        if unknown:
            raise ValueError(f"Unknown configuration keys: {', '.join(unknown)}")
        for key in ("workbook_path", "input_path", "output_dir"):
            value = raw.get(key, "outputs" if key == "output_dir" else None)
            if value is not None:
                candidate = Path(value).expanduser()
                raw[key] = candidate if candidate.is_absolute() else config_path.parent / candidate
        if "accepted_statuses" in raw:
            if not isinstance(raw["accepted_statuses"], (list, tuple)):
                raise ValueError("accepted_statuses must be a list")
            raw["accepted_statuses"] = tuple(raw["accepted_statuses"])
        # Old example YAMLs left the source year null. The published curves use 2021.
        if raw.get("cost_reference_year") is None:
            raw["cost_reference_year"] = 2021
        result = cls(**raw)
        result.validate(require_input=require_input)
        return result

    def validate(self, *, require_input: bool = True) -> None:
        if self.input_path is not None and self.workbook_path is not None:
            raise ValueError("Use input_path or workbook_path, not both")
        if require_input:
            if not self.source_path.is_file():
                raise FileNotFoundError(f"Input not found: {self.source_path}")
            if self.source_path.suffix.lower() not in {".xlsx", ".csv", ".parquet", ".geoparquet"}:
                raise ValueError("Input must be XLSX, CSV, Parquet or GeoParquet")
        if self.input_layout not in {"auto", "routes", "matrix"}:
            raise ValueError("input_layout must be auto, routes or matrix")
        if self.input_row is not None and (
            isinstance(self.input_row, bool) or not isinstance(self.input_row, int) or self.input_row < 1
        ):
            raise ValueError("input_row must be a positive integer (first data row = 1)")
        for key in ("require_annual_flow", "write_xlsx", "write_costed_input"):
            if not isinstance(getattr(self, key), bool):
                raise ValueError(f"{key} must be true or false")
        if not (self.write_xlsx or self.write_costed_input):
            raise ValueError("Enable at least one output")
        if self.currency != "EUR":
            raise ValueError("These regressions use EUR; no foreign-exchange conversion is applied")
        if type(self.cost_reference_year) is not int or self.cost_reference_year != 2021:
            raise ValueError("cost_reference_year must be 2021; supply regression coefficients in EUR 2021")
        if self.eur2021_to_eur2024_factor is not None:
            if finite_nonnegative(self.eur2021_to_eur2024_factor, "eur2021_to_eur2024_factor") <= 0:
                raise ValueError("eur2021_to_eur2024_factor must be greater than zero")
            if not isinstance(self.eur2024_factor_source, str) or not self.eur2024_factor_source.strip():
                raise ValueError("Document a custom factor with eur2024_factor_source")
        elif self.eur2024_factor_source is not None:
            raise ValueError("eur2024_factor_source requires eur2021_to_eur2024_factor")
        if not isinstance(self.accepted_statuses, (tuple, list)) or not self.accepted_statuses:
            raise ValueError("accepted_statuses must be a nonempty list")
        if any(not isinstance(s, str) or not s.strip() for s in self.accepted_statuses):
            raise ValueError("accepted_statuses must contain nonblank strings")
        if self.default_transport_mode is not None:
            normalize_mode(self.default_transport_mode)
        for key in ("from_id_column", "to_id_column", "distance_column", "annual_flow_column", "route_metrics_sheet"):
            if not isinstance(getattr(self, key), str) or not getattr(self, key).strip():
                raise ValueError(f"{key} must be a nonblank string")
        for key in ("mode_column", "status_column"):
            value = getattr(self, key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{key} must be a nonblank string or null")
        for key in ("output_workbook_name", "combined_output_workbook", "truck_output_workbook", "railway_output_workbook"):
            value = getattr(self, key)
            if value is not None and (not isinstance(value, str) or Path(value).name != value or not value.lower().endswith(".xlsx")):
                raise ValueError(f"{key} must be an .xlsx filename without directories")
        for mode in ("truck", "railway"):
            self.parameters(mode).validate()
        if self.annual_flow_t_per_year is not None:
            finite_nonnegative(self.annual_flow_t_per_year, "annual_flow_t_per_year")

    def parameters(self, mode: str) -> ModeParameters:
        mode = normalize_mode(mode)
        fixed = finite_nonnegative(getattr(self, f"{mode}_fixed_eur_per_t"), f"{mode}_fixed_eur_per_t")
        rate = finite_nonnegative(getattr(self, f"{mode}_distance_rate_eur_per_t_km"), f"{mode}_distance_rate_eur_per_t_km")
        return ModeParameters(mode, fixed, rate)

    @property
    def conversion_factor(self) -> float:
        return DEFAULT_FACTOR if self.eur2021_to_eur2024_factor is None else float(self.eur2021_to_eur2024_factor)

    def source_sheet(self, mode: str) -> str:
        return getattr(self, f"{normalize_mode(mode)}_sheet")

    def output_name(self, modes: tuple[str, ...]) -> str:
        return getattr(self, f"{modes[0]}_output_workbook") if len(modes) == 1 else self.combined_output_workbook

    def report_name(self, modes: tuple[str, ...]) -> str:
        return self.output_workbook_name or (f"{modes[0]}_cost_results.xlsx" if len(modes) == 1 else "container_transport_cost_results.xlsx")


def normalize_mode(value) -> str:
    name = str(value).strip().lower()
    name = {"road": "truck", "rail": "railway", "train": "railway"}.get(name, name)
    if name not in {"truck", "railway"}:
        raise ValueError(f"Unsupported transport mode: {value!r}; use truck or railway")
    return name


def finite_nonnegative(value, name: str) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result
