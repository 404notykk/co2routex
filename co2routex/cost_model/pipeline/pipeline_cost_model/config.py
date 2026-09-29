"""Settings for direct benchmarks and annual-flow cost approximations."""
from __future__ import annotations
import math
import warnings
from dataclasses import dataclass, fields
from pathlib import Path
import yaml
from .currency import DEFAULT_FACTOR


@dataclass(frozen=True)
class PipelineCostConfig:
    input_path: Path | None = None
    workbook_path: Path | None = None  # Version 0.1 input alias.
    output_dir: Path = Path("outputs/pipeline_costs")
    output_workbook_name: str = "pipeline_cost_results.xlsx"
    route_metrics_sheet: str = "pipeline_route_metrics"
    mode: str = "approximation"
    coefficient_method: str = "flow_range"  # Legacy default; shipped configs use fixed_design.
    from_id_column: str = "from_id"
    to_id_column: str = "to_id"
    distance_column: str = "distance_km"
    resistance_column: str = "average_route_resistance"
    annual_flow_column: str = "annual_flow_t_per_year"
    input_row: int | None = None  # 1-based data-row position, before route filters.
    benchmark_annual_flow_t_per_year: float | None = None
    annual_flow_min_t_per_year: float | None = None
    annual_flow_max_t_per_year: float | None = None
    capacity_min_t_per_h: float = 18.0
    capacity_max_t_per_h: float = 4050.0
    capacity_range_basis: str = "temporary_DN_square_law_proxy"
    fit_method: str = "endpoints"
    fit_samples: int = 9
    validation_samples: int = 8
    fit_error_warning_percent: float = 10.0
    write_xlsx: bool = True
    write_costed_input: bool = True
    write_csv: bool = False
    write_json: bool = False
    terrain: str = "Onshore"
    timeframe: str = "mid-term"
    electricity_price_eur_per_mwh: float = 60.0
    electricity_price_reference_year: int = 2021
    operating_hours_per_year: float = 8000.0
    p_inlet_bar: float = 10.0
    p_outlet_bar: float = 70.0
    discount_rate: float = 0.10
    currency: str = "EUR"
    cost_reference_year: int = 2021
    eur2021_to_eur2024_factor: float | None = None
    eur2024_factor_source: str | None = None

    @property
    def conversion_factor(self) -> float:
        return DEFAULT_FACTOR if self.eur2021_to_eur2024_factor is None else float(self.eur2021_to_eur2024_factor)

    @property
    def source_path(self) -> Path | None:
        return self.input_path if self.input_path is not None else self.workbook_path

    @property
    def needs_reference_flow(self) -> bool:
        return self.mode != "approximation" or self.coefficient_method == "fixed_design"

    @property
    def uses_flow_range(self) -> bool:
        return self.mode != "benchmark" and self.coefficient_method == "flow_range"

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PipelineCostConfig":
        config_path = Path(path).expanduser().resolve()
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("The YAML configuration must contain a mapping")
        # Accept old YAMLs without retaining the removed financial adjustment.
        if "spatial_fixed_opex_mode" in raw:
            raw.pop("spatial_fixed_opex_mode")
            warnings.warn("Ignoring removed setting 'spatial_fixed_opex_mode': version 0.4 calculates baseline costs only.", UserWarning)
        if "detailed_workbook_name" in raw:
            raw["output_workbook_name"] = raw.pop("detailed_workbook_name")
            warnings.warn("Using the legacy detailed_workbook_name for the single report workbook. The costed input copy is named automatically.", UserWarning)
        unknown = sorted(set(raw) - {f.name for f in fields(cls)})
        if unknown:
            raise ValueError(f"Unknown configuration keys: {', '.join(unknown)}")
        values = dict(raw)
        values.setdefault("output_dir", "outputs/pipeline_costs")
        for key in ("input_path", "workbook_path", "output_dir"):
            if values.get(key) is not None:
                candidate = Path(values[key]).expanduser()
                values[key] = (config_path.parent / candidate).resolve()
        return cls(**values)  # Validate after optional CLI mode overrides.

    def validate(self, *, require_input: bool = True) -> None:
        if self.input_path is not None and self.workbook_path is not None:
            raise ValueError("Use input_path or workbook_path, not both")
        if require_input:
            path = self.source_path
            if path is None or not path.is_file():
                raise FileNotFoundError(f"Input file not found: {path}")
            if path.suffix.lower() not in {".xlsx", ".csv", ".parquet", ".geoparquet"}:
                raise ValueError("Input must be .xlsx, .csv, .parquet or .geoparquet")
        for name in ("from_id_column", "to_id_column", "distance_column", "resistance_column", "annual_flow_column"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonblank column name")
        if self.input_row is not None:
            if isinstance(self.input_row, bool) or not isinstance(self.input_row, int) or self.input_row < 1:
                raise ValueError("input_row must be an integer >= 1 (first data row = 1)")
        if self.mode not in {"approximation", "benchmark", "both"}:
            raise ValueError("mode must be approximation, benchmark or both")
        if self.coefficient_method not in {"fixed_design", "flow_range"}:
            raise ValueError("coefficient_method must be fixed_design or flow_range")
        if self.terrain not in {"Onshore", "Offshore"}:
            raise ValueError("terrain must be Onshore or Offshore")
        if self.timeframe not in {"near-term", "mid-term", "long-term"}:
            raise ValueError("Unsupported timeframe")
        for name in ("operating_hours_per_year", "discount_rate", "p_inlet_bar", "p_outlet_bar"):
            positive(getattr(self, name), name)
        if self.operating_hours_per_year > 8760:
            raise ValueError("operating_hours_per_year must not exceed 8760")
        if not math.isfinite(self.electricity_price_eur_per_mwh) or self.electricity_price_eur_per_mwh < 0:
            raise ValueError("Electricity price must be finite and nonnegative")
        if not 30 <= self.p_outlet_bar < 90 or not self.p_inlet_bar < self.p_outlet_bar:
            raise ValueError("Supported pressure domain: 30 <= outlet < 90 bar, with feed inlet < outlet")
        if self.currency != "EUR" or self.cost_reference_year != 2021:
            raise ValueError("The supplied cost data support only EUR 2021")
        if type(self.electricity_price_reference_year) is not int or self.electricity_price_reference_year != 2021:
            raise ValueError("electricity_price_reference_year must be 2021; supply electricity_price_eur_per_mwh in EUR 2021/MWh")
        if self.eur2021_to_eur2024_factor is not None:
            factor = self.eur2021_to_eur2024_factor
            if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not math.isfinite(factor) or factor <= 0:
                raise ValueError("eur2021_to_eur2024_factor must be finite and greater than zero")
            if not isinstance(self.eur2024_factor_source, str) or not self.eur2024_factor_source.strip():
                raise ValueError("Document a custom factor with eur2024_factor_source")
        elif self.eur2024_factor_source is not None:
            raise ValueError("eur2024_factor_source requires eur2021_to_eur2024_factor")
        for setting in ("write_xlsx", "write_costed_input", "write_csv", "write_json"):
            if not isinstance(getattr(self, setting), bool):
                raise ValueError(f"{setting} must be true or false")
        if not any((self.write_xlsx, self.write_costed_input, self.write_csv, self.write_json)):
            raise ValueError("Enable at least one output: write_xlsx, write_costed_input, write_csv or write_json")
        if Path(self.output_workbook_name).name != self.output_workbook_name or Path(self.output_workbook_name).suffix.lower() != ".xlsx":
            raise ValueError("output_workbook_name must be a plain .xlsx filename")
        if self.benchmark_annual_flow_t_per_year is not None and self.needs_reference_flow:
            positive(self.benchmark_annual_flow_t_per_year, "benchmark annual flow")
        if self.uses_flow_range:
            if self.fit_method not in {"endpoints", "least_squares"}:
                raise ValueError("fit_method must be endpoints or least_squares")
            for name, minimum in (("fit_samples", 2), ("validation_samples", 1)):
                value = getattr(self, name)
                if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                    raise ValueError(f"{name} must be an integer >= {minimum}")
            positive(self.fit_error_warning_percent, "fit_error_warning_percent")
            if (self.annual_flow_min_t_per_year is None) != (self.annual_flow_max_t_per_year is None):
                raise ValueError("Specify both annual flow bounds or neither")
            if self.annual_flow_min_t_per_year is not None:
                valid_bounds(self.annual_flow_min_t_per_year, self.annual_flow_max_t_per_year)
            else:
                valid_bounds(self.capacity_min_t_per_h, self.capacity_max_t_per_h)

    def flow_bounds(self, route) -> tuple[float, float, str]:
        if route.annual_flow_min_t_per_year is not None:
            lo, hi = route.annual_flow_min_t_per_year, route.annual_flow_max_t_per_year
            basis = "route_annual_flow_bounds"
        elif self.annual_flow_min_t_per_year is not None:
            lo, hi = self.annual_flow_min_t_per_year, self.annual_flow_max_t_per_year
            basis = "configured_annual_flow_bounds"
        else:
            lo = self.capacity_min_t_per_h * self.operating_hours_per_year
            hi = self.capacity_max_t_per_h * self.operating_hours_per_year
            basis = self.capacity_range_basis + "_converted_to_annual_flow"
        valid_bounds(lo, hi)
        return float(lo), float(hi), basis


def positive(value: float, name: str) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")


def valid_bounds(lo: float, hi: float) -> None:
    positive(lo, "minimum flow")
    positive(hi, "maximum flow")
    if hi <= lo:
        raise ValueError("Maximum flow must exceed minimum flow")
