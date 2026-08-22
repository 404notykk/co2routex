"""Command-line interface for the A* raster router."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import yaml

from .models import Settings
from .pipeline import run


RASTER_SUFFIXES = {".tif", ".tiff"}
WORKBOOK_SUFFIX = ".xlsx"


def _resolve(base: Path, value: str) -> Path:
    """Resolve a path relative to the configuration file."""
    path = Path(value).expanduser()

    if path.is_absolute():
        return path.resolve()

    return (base / path).resolve()


def _required_path(
    config: dict[str, Any],
    key: str,
    base: Path,
) -> Path:
    """Read and resolve a required path from the configuration."""
    value = config.get(key)

    if value is None or str(value).strip() == "":
        raise ValueError(
            f"Configuration value '{key}' is required"
        )

    return _resolve(base, str(value))


def _read_string(
    config: dict[str, Any],
    key: str,
    default: str,
) -> str:
    """Read and validate a non-empty string setting."""
    value = config.get(key, default)

    if value is None:
        raise ValueError(
            f"Configuration value '{key}' cannot be empty"
        )

    result = str(value).strip()

    if not result:
        raise ValueError(
            f"Configuration value '{key}' cannot be empty"
        )

    return result


def _read_boolean(
    config: dict[str, Any],
    key: str,
    default: bool,
) -> bool:
    """Read a Boolean configuration value with strict validation."""
    value = config.get(key, default)

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized in {"true", "yes", "on", "1"}:
            return True

        if normalized in {"false", "no", "off", "0"}:
            return False

    raise ValueError(
        f"Configuration value '{key}' must be true or false"
    )


def _read_integer(
    config: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int | None = None,
) -> int:
    """Read and validate an integer configuration value."""
    value = config.get(key, default)

    if isinstance(value, bool):
        raise ValueError(
            f"Configuration value '{key}' must be an integer"
        )

    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        normalized = value.strip()

        try:
            result = int(normalized)
        except ValueError as error:
            raise ValueError(
                f"Configuration value '{key}' must be an integer"
            ) from error
    else:
        raise ValueError(
            f"Configuration value '{key}' must be an integer"
        )

    if minimum is not None and result < minimum:
        raise ValueError(
            f"Configuration value '{key}' must be at least {minimum}"
        )

    return result


def _read_mode_sheets(
    config: dict[str, Any],
    pipeline_sheet: str,
) -> tuple[str, ...]:
    """
    Read the transport-mode worksheet names.

    If mode_sheets is absent, fall back to pipeline_sheet for
    compatibility with the original pipeline-only configuration.
    """
    value = config.get("mode_sheets")

    if value is None:
        return (pipeline_sheet,)

    if isinstance(value, str):
        raise ValueError(
            "Configuration value 'mode_sheets' must be a YAML list, "
            "for example:\nmode_sheets:\n  - pipeline"
        )

    if not isinstance(value, (list, tuple)):
        raise ValueError(
            "Configuration value 'mode_sheets' must be a list"
        )

    mode_sheets: list[str] = []

    for index, sheet in enumerate(value):
        if not isinstance(sheet, str) or not sheet.strip():
            raise ValueError(
                "Every entry in 'mode_sheets' must be a "
                f"non-empty string; invalid entry at index {index}"
            )

        mode_sheets.append(sheet.strip())

    if not mode_sheets:
        raise ValueError(
            "Configuration value 'mode_sheets' must contain "
            "at least one worksheet name"
        )

    if len(set(mode_sheets)) != len(mode_sheets):
        raise ValueError(
            "Configuration value 'mode_sheets' cannot contain "
            "duplicate worksheet names"
        )

    return tuple(mode_sheets)


def _validate_input_files(
    raster_path: Path,
    workbook_path: Path,
) -> None:
    """Validate the required input files and their extensions."""
    if not raster_path.exists():
        raise FileNotFoundError(
            f"Spatial resistance raster does not exist: {raster_path}"
        )

    if not raster_path.is_file():
        raise ValueError(
            f"Raster path is not a file: {raster_path}"
        )

    if raster_path.suffix.lower() not in RASTER_SUFFIXES:
        raise ValueError(
            "The spatial resistance raster must be a "
            ".tif or .tiff file"
        )

    if not workbook_path.exists():
        raise FileNotFoundError(
            f"Input workbook does not exist: {workbook_path}"
        )

    if not workbook_path.is_file():
        raise ValueError(
            f"Workbook path is not a file: {workbook_path}"
        )

    if workbook_path.suffix.lower() != WORKBOOK_SUFFIX:
        raise ValueError(
            "The routing input workbook must be an .xlsx file"
        )


def load_settings(config_path: Path) -> Settings:
    """Load and validate router settings from a YAML file."""
    config_path = config_path.expanduser().resolve()

    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file does not exist: {config_path}"
        )

    if not config_path.is_file():
        raise ValueError(
            f"Configuration path is not a file: {config_path}"
        )

    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    if not isinstance(config, dict):
        raise ValueError(
            "The configuration file must contain a YAML mapping"
        )

    # Relative input and output paths are anchored to the directory
    # containing the YAML configuration file.
    base = config_path.parent

    raster_path = _required_path(
        config,
        "raster_path",
        base,
    )
    workbook_path = _required_path(
        config,
        "workbook_path",
        base,
    )
    output_dir = _required_path(
        config,
        "output_dir",
        base,
    )

    _validate_input_files(
        raster_path,
        workbook_path,
    )

    if output_dir.exists() and not output_dir.is_dir():
        raise NotADirectoryError(
            f"Output path exists but is not a directory: {output_dir}"
        )

    nodes_sheet = _read_string(
        config,
        "nodes_sheet",
        "nodes",
    )

    pipeline_sheet = _read_string(
        config,
        "pipeline_sheet",
        "pipeline",
    )

    mode_sheets = _read_mode_sheets(
        config,
        pipeline_sheet,
    )

    nodes_crs = _read_string(
        config,
        "nodes_crs",
        "EPSG:4326",
    )

    connectivity = _read_integer(
        config,
        "connectivity",
        8,
    )

    if connectivity not in {4, 8}:
        raise ValueError(
            "Configuration value 'connectivity' must be 4 or 8"
        )

    snap_radius_cells = _read_integer(
        config,
        "snap_radius_cells",
        10,
        minimum=0,
    )

    output_workbook_name = _read_string(
        config,
        "output_workbook_name",
        "node_metrics_routed.xlsx",
    )

    routes_filename = _read_string(
        config,
        "routes_filename",
        "routes.gpkg",
    )

    return Settings(
        raster_path=raster_path,
        workbook_path=workbook_path,
        output_dir=output_dir,
        nodes_sheet=nodes_sheet,
        pipeline_sheet=pipeline_sheet,
        mode_sheets=mode_sheets,
        nodes_crs=nodes_crs,
        connectivity=connectivity,
        prevent_corner_cutting=_read_boolean(
            config,
            "prevent_corner_cutting",
            True,
        ),
        snap_radius_cells=snap_radius_cells,
        zero_is_barrier=_read_boolean(
            config,
            "zero_is_barrier",
            False,
        ),
        cache_reverse_routes=_read_boolean(
            config,
            "cache_reverse_routes",
            True,
        ),
        output_workbook_name=output_workbook_name,
        routes_filename=routes_filename,
    )


def main() -> None:
    """Run the A* raster-routing command-line interface."""
    parser = argparse.ArgumentParser(
        description=(
            "Calculate spatially least-resistant CO2 routes "
            "using A* on a resistance raster"
        )
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help=(
            "Path to the YAML configuration file "
            "(default: config.yaml)"
        ),
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    settings = load_settings(args.config)

    logging.info(
        "Starting A* raster routing using workbook: %s",
        settings.workbook_path,
    )

    logging.info(
        "Mode worksheets selected for processing: %s",
        ", ".join(settings.mode_sheets),
    )

    records = run(settings)

    succeeded = sum(
        record.get("status") == "ok"
        for record in records
    )
    failed = len(records) - succeeded

    logging.info(
        "Routing completed: %s successful, %s failed",
        succeeded,
        failed,
    )

    print(
        f"Completed {succeeded}/{len(records)} routes. "
        f"Outputs: {settings.output_dir}"
    )


if __name__ == "__main__":
    main()