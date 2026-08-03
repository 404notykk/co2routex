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

    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Configuration value '{key}' must be an integer"
        ) from error

    if minimum is not None and result < minimum:
        raise ValueError(
            f"Configuration value '{key}' must be at least {minimum}"
        )

    return result


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

    pipeline_sheet = str(
        config.get("pipeline_sheet", "pipeline")
    ).strip()

    if not pipeline_sheet:
        raise ValueError(
            "Configuration value 'pipeline_sheet' cannot be empty"
        )

    nodes_crs = str(
        config.get("nodes_crs", "EPSG:4326")
    ).strip()

    if not nodes_crs:
        raise ValueError(
            "Configuration value 'nodes_crs' cannot be empty"
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

    settings = Settings(
        raster_path=raster_path,
        workbook_path=workbook_path,
        output_dir=output_dir,

        # The A* raster router processes only the pipeline worksheet.
        mode_sheets=[pipeline_sheet],

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

        # These fields remain temporarily for compatibility with the
        # existing Settings model. The CLI does not support CSV input.
        nodes_csv=None,
        mode_csvs=None,
    )

    return settings


def main() -> None:
    """Run the A* raster-routing command-line interface."""
    parser = argparse.ArgumentParser(
        description=(
            "Calculate spatially least-resistant CO2 pipeline routes "
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

    records = run(settings)

    succeeded = sum(
        record["status"] == "ok"
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