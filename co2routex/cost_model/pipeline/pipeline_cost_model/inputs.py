"""Load fixed connections and explicit transported annual flows."""
from __future__ import annotations
import math
import pandas as pd
from .models import RouteInput
from .config import PipelineCostConfig, valid_bounds


def read_input_frame(config: PipelineCostConfig, *, for_export=False) -> pd.DataFrame:
    """Read tabular attributes; retain physical row positions independently of a saved index.

    Parquet cost input projects only relevant columns. The Excel preview omits
    geometry and nested/binary columns. A selected Parquet row reads only its
    containing row group, including when the file has a nonstandard pandas index.
    """
    path = config.source_path
    if path is None:
        raise ValueError("An input file is required")
    if config.input_row is not None and (
        isinstance(config.input_row, bool) or not isinstance(config.input_row, int) or config.input_row < 1
    ):
        raise ValueError("input_row must be an integer >= 1 (first data row = 1)")
    identifiers = {config.from_id_column: str, config.to_id_column: str, "scenario_id": str}
    relevant = {
        config.from_id_column, config.to_id_column, config.distance_column,
        config.resistance_column, config.annual_flow_column, "scenario_id", "terrain", "status", "mode",
        "annual_flow_min_t_per_year", "annual_flow_max_t_per_year",
    }
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ValueError("Parquet input requires pyarrow. Install with: python -m pip install 'pyarrow>=12'") from exc
        with pq.ParquetFile(path) as parquet:
            columns = [name for name in parquet.schema_arrow.names if name in relevant]
            if for_export:
                excluded_types = (
                    pa.types.is_binary, pa.types.is_large_binary, pa.types.is_fixed_size_binary,
                    pa.types.is_struct, pa.types.is_list, pa.types.is_large_list,
                    pa.types.is_fixed_size_list, pa.types.is_map, pa.types.is_union,
                )
                columns = [field.name for field in parquet.schema_arrow
                           if field.name.lower() != "geometry"
                           and not any(check(field.type) for check in excluded_types)]
            offset = 0
            if config.input_row is None:
                table = parquet.read(columns=columns)
            else:
                position = config.input_row - 1
                total = parquet.metadata.num_rows
                if position >= total:
                    raise ValueError(f"input_row {config.input_row} is out of range; the file has {total} data rows")
                for group in range(parquet.num_row_groups):
                    count = parquet.metadata.row_group(group).num_rows
                    if position < offset + count:
                        table = parquet.read_row_group(group, columns=columns).slice(position - offset, 1)
                        offset = position
                        break
                    offset += count
            # Stored pandas index values never determine which physical row is selected.
            # Parquet column projection can also return a nested parent when a
            # flat name such as bbox.xmin matches a nested path. Keep exact fields.
            frame = table.select(columns).to_pandas(ignore_metadata=True)
            frame.index = pd.RangeIndex(offset, offset + len(frame))
            return frame
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path, dtype=identifiers, keep_default_na=False)
    elif path.suffix.lower() == ".xlsx":
        frame = pd.read_excel(path, sheet_name=config.route_metrics_sheet, dtype=identifiers, keep_default_na=False)
    else:
        raise ValueError("Input must be .xlsx, .csv, .parquet or .geoparquet")
    frame = frame.reset_index(drop=True)
    if config.input_row is not None:
        if config.input_row > len(frame):
            raise ValueError(f"input_row {config.input_row} is out of range; the file has {len(frame)} data rows")
        frame = frame.iloc[[config.input_row - 1]]
    return frame


def read_routes(config: PipelineCostConfig) -> list[RouteInput]:
    frame = read_input_frame(config)
    missing = sorted({config.from_id_column, config.to_id_column, config.distance_column} - set(frame.columns))
    if missing:
        raise ValueError(f"Missing input columns: {', '.join(missing)}")
    routes, seen = [], set()
    for index, row in frame.iterrows():
        if "status" in frame and str(row["status"]).strip().lower() != "ok":
            continue
        if "mode" in frame and str(row["mode"]).strip().lower() != "pipeline":
            continue
        source_row = int(index) + 2
        number = source_row - 1
        scenario = clean_id(row.get("scenario_id")) if present(row.get("scenario_id")) else f"row_{source_row}"
        if scenario in seen:
            raise ValueError(f"Duplicate scenario_id {scenario!r}")
        seen.add(scenario)
        flow = number_or_none(row.get(config.annual_flow_column), config.annual_flow_column, number)
        if flow is None:
            flow = config.benchmark_annual_flow_t_per_year
        if config.needs_reference_flow and flow is None:
            raise ValueError(
                f"Data row {number}: benchmark requires {config.annual_flow_column!r}, as do fixed_design coefficients. "
                "Supply transported/captured tonnes per year; gross emissions are not substituted."
            )
        lo = hi = None
        if config.uses_flow_range:
            lo = number_or_none(row.get("annual_flow_min_t_per_year"), "annual_flow_min_t_per_year", number)
            hi = number_or_none(row.get("annual_flow_max_t_per_year"), "annual_flow_max_t_per_year", number)
            if (lo is None) != (hi is None):
                raise ValueError(f"Data row {number}: specify both annual flow bounds or neither")
            if lo is not None:
                valid_bounds(lo, hi)
        resistance = number_or_none(row.get(config.resistance_column), config.resistance_column, number)
        terrain = str(row["terrain"]).strip() if present(row.get("terrain")) else None
        if terrain is not None and terrain not in {"Onshore", "Offshore"}:
            raise ValueError(f"Data row {number}: terrain must be Onshore or Offshore")
        distance = number_or_none(row[config.distance_column], config.distance_column, number)
        if distance is None:
            raise ValueError(f"Data row {number}: missing {config.distance_column}")
        routes.append(RouteInput(
            clean_id(row[config.from_id_column]), clean_id(row[config.to_id_column]), distance,
            1.0 if resistance is None else resistance, scenario, flow, lo, hi, terrain, source_row,
        ))
    if not routes:
        if config.input_row is not None:
            raise ValueError(f"Input data row {config.input_row} was excluded by the status='ok' or mode='pipeline' filter; no other row was substituted")
        raise ValueError("No successful pipeline routes found")
    return routes


def present(value) -> bool:
    return value is not None and not pd.isna(value) and str(value).strip() != ""


def clean_id(value) -> str:
    if not present(value):
        raise ValueError("Node IDs cannot be blank")
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def number_or_none(value, name, row_number):
    if not present(value):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Data row {row_number}: invalid {name}") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"Data row {row_number}: {name} must be finite and positive")
    return number
