"""Write the routed Excel workbook and GIS route output."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import fiona
from openpyxl import load_workbook
from pyproj import CRS

from .models import Settings


ROUTE_LAYER_NAME = "pipeline_routes"

GPKG_SCHEMA = {
    "geometry": "LineString",
    "properties": {
        "mode": "str",
        "from_id": "str",
        "to_id": "str",
        "from_name": "str",
        "to_name": "str",
        "status": "str",
        "distance_km": "float",
        "accumulated_resistance": "float",
        "explored_cells": "int",
        "path_cells": "int",
        "from_snap_distance_m": "float",
        "to_snap_distance_m": "float",
        "from_longitude": "float",
        "from_latitude": "float",
        "to_longitude": "float",
        "to_latitude": "float",
    },
}


def write_outputs(
    settings: Settings,
    route_records: list[dict[str, Any]],
    raster_crs,
) -> tuple[Path, Path]:
    """
    Write the two routing outputs.

    The outputs are:

    1. An updated copy of the input XLSX workbook. Successful entries in the
       pipeline connection matrix are replaced with routed distances in km.
    2. A GeoPackage containing successful routes in the raster CRS.

    Failed routes remain unchanged in the workbook.

    Returns
    -------
    tuple[Path, Path]
        Paths to the updated workbook and GeoPackage.
    """
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    workbook_output = settings.output_dir / settings.output_workbook_name
    routes_output = settings.output_dir / settings.routes_filename

    _validate_output_paths(
        input_workbook=settings.workbook_path,
        workbook_output=workbook_output,
        routes_output=routes_output,
    )

    successful_records = _successful_pipeline_records(
        route_records,
        settings.pipeline_sheet,
    )

    temporary_workbook = _temporary_path(workbook_output)
    temporary_routes = _temporary_path(routes_output)

    try:
        _write_updated_workbook(
            source_path=settings.workbook_path,
            output_path=temporary_workbook,
            pipeline_sheet=settings.pipeline_sheet,
            successful_records=successful_records,
        )

        _write_geopackage(
            path=temporary_routes,
            successful_records=successful_records,
            raster_crs=raster_crs,
        )

        os.replace(temporary_workbook, workbook_output)
        os.replace(temporary_routes, routes_output)

    finally:
        temporary_workbook.unlink(missing_ok=True)
        temporary_routes.unlink(missing_ok=True)

    return workbook_output, routes_output


def _successful_pipeline_records(
    route_records: list[dict[str, Any]],
    pipeline_sheet: str,
) -> list[dict[str, Any]]:
    """Select and validate successful pipeline-route records."""
    successful: list[dict[str, Any]] = []
    seen_connections: set[tuple[str, str]] = set()

    for record in route_records:
        if record.get("status") != "ok":
            continue

        mode = str(record.get("mode", "")).strip()
        if mode != pipeline_sheet:
            continue

        from_id = _clean_id(record.get("from_id"))
        to_id = _clean_id(record.get("to_id"))

        if from_id == to_id:
            raise ValueError(
                f"A successful route cannot connect node {from_id} to itself"
            )

        connection = (from_id, to_id)
        if connection in seen_connections:
            raise ValueError(
                f"Duplicate successful route record: {from_id} -> {to_id}"
            )

        distance_km = _required_nonnegative_float(
            record.get("distance_km"),
            f"distance_km for route {from_id} -> {to_id}",
        )

        coordinates = _validate_coordinates(
            record.get("coordinates"),
            from_id,
            to_id,
        )

        copied = dict(record)
        copied["mode"] = mode
        copied["from_id"] = from_id
        copied["to_id"] = to_id
        copied["distance_km"] = distance_km
        copied["coordinates"] = coordinates

        successful.append(copied)
        seen_connections.add(connection)

    return successful


def _write_updated_workbook(
    source_path: Path,
    output_path: Path,
    pipeline_sheet: str,
    successful_records: list[dict[str, Any]],
) -> None:
    """
    Copy the input workbook and update successful pipeline-matrix entries.

    Existing formatting and all workbook sheets are retained.
    """
    workbook = load_workbook(source_path)

    if pipeline_sheet not in workbook.sheetnames:
        workbook.close()
        raise ValueError(
            f"Workbook does not contain the pipeline sheet "
            f"{pipeline_sheet!r}"
        )

    sheet = workbook[pipeline_sheet]

    column_by_node = _matrix_columns(sheet)
    row_by_node = _matrix_rows(sheet)

    for record in successful_records:
        from_id = record["from_id"]
        to_id = record["to_id"]

        if from_id not in row_by_node:
            workbook.close()
            raise ValueError(
                f"Pipeline matrix does not contain row node {from_id!r}"
            )

        if to_id not in column_by_node:
            workbook.close()
            raise ValueError(
                f"Pipeline matrix does not contain column node {to_id!r}"
            )

        row_number = row_by_node[from_id]
        column_number = column_by_node[to_id]
        cell = sheet.cell(row=row_number, column=column_number)

        _validate_permitted_matrix_cell(
            cell.value,
            pipeline_sheet,
            from_id,
            to_id,
        )

        cell.value = record["distance_km"]
        cell.number_format = "0.000"

    workbook.save(output_path)
    workbook.close()


def _matrix_columns(sheet) -> dict[str, int]:
    """Map node IDs in the matrix header row to Excel column numbers."""
    columns: dict[str, int] = {}

    for column_number in range(2, sheet.max_column + 1):
        value = sheet.cell(row=1, column=column_number).value

        if value is None or str(value).strip() == "":
            continue

        node_id = _clean_id(value)

        if node_id in columns:
            raise ValueError(
                f"Pipeline matrix contains duplicate column ID {node_id!r}"
            )

        columns[node_id] = column_number

    if not columns:
        raise ValueError("Pipeline matrix contains no column node IDs")

    return columns


def _matrix_rows(sheet) -> dict[str, int]:
    """Map node IDs in the first matrix column to Excel row numbers."""
    rows: dict[str, int] = {}

    for row_number in range(2, sheet.max_row + 1):
        value = sheet.cell(row=row_number, column=1).value

        if value is None or str(value).strip() == "":
            continue

        node_id = _clean_id(value)

        if node_id in rows:
            raise ValueError(
                f"Pipeline matrix contains duplicate row ID {node_id!r}"
            )

        rows[node_id] = row_number

    if not rows:
        raise ValueError("Pipeline matrix contains no row node IDs")

    return rows


def _validate_permitted_matrix_cell(
    value: Any,
    sheet_name: str,
    from_id: str,
    to_id: str,
) -> None:
    """
    Confirm that a routed connection was permitted in the input matrix.

    Any positive numeric value permits a connection. Zero, blank, negative,
    Boolean and non-numeric values are rejected.
    """
    label = (
        f"{sheet_name} matrix entry for route "
        f"{from_id} -> {to_id}"
    )

    if value is None or isinstance(value, bool):
        raise ValueError(f"{label} is not a permitted connection")

    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{label} must contain a positive numeric value"
        ) from exc

    if not math.isfinite(numeric_value) or numeric_value <= 0:
        raise ValueError(
            f"{label} must be greater than zero before it can be updated"
        )


def _write_geopackage(
    path: Path,
    successful_records: list[dict[str, Any]],
    raster_crs,
) -> None:
    """Write successful pipeline routes to a GeoPackage."""
    crs = CRS.from_user_input(raster_crs)

    if not crs.is_projected:
        raise ValueError(
            "The route GeoPackage requires the projected raster CRS"
        )

    with fiona.open(
        path,
        mode="w",
        driver="GPKG",
        layer=ROUTE_LAYER_NAME,
        schema=GPKG_SCHEMA,
        crs_wkt=crs.to_wkt(),
        encoding="UTF-8",
    ) as destination:
        for record in successful_records:
            coordinates = record["coordinates"]

            # A LineString requires at least two coordinate positions.
            # Distinct nodes may occasionally snap to the same raster cell.
            if len(coordinates) == 1:
                coordinates = [coordinates[0], coordinates[0]]

            destination.write(
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coordinates,
                    },
                    "properties": _route_properties(record),
                }
            )


def _route_properties(record: dict[str, Any]) -> dict[str, Any]:
    """Create GeoPackage-compatible route attributes."""
    return {
        "mode": _optional_string(record.get("mode")),
        "from_id": _optional_string(record.get("from_id")),
        "to_id": _optional_string(record.get("to_id")),
        "from_name": _optional_string(record.get("from_name")),
        "to_name": _optional_string(record.get("to_name")),
        "status": _optional_string(record.get("status")),
        "distance_km": _optional_float(record.get("distance_km")),
        "accumulated_resistance": _optional_float(
            record.get("accumulated_resistance")
        ),
        "explored_cells": _optional_int(record.get("explored_cells")),
        "path_cells": _optional_int(record.get("path_cells")),
        "from_snap_distance_m": _optional_float(
            record.get("from_snap_distance_m")
        ),
        "to_snap_distance_m": _optional_float(
            record.get("to_snap_distance_m")
        ),
        "from_longitude": _optional_float(record.get("from_longitude")),
        "from_latitude": _optional_float(record.get("from_latitude")),
        "to_longitude": _optional_float(record.get("to_longitude")),
        "to_latitude": _optional_float(record.get("to_latitude")),
    }


def _validate_coordinates(
    value: Any,
    from_id: str,
    to_id: str,
) -> list[tuple[float, float]]:
    """Validate route coordinates and convert them to plain floats."""
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(
            f"Route {from_id} -> {to_id} contains no coordinates"
        )

    coordinates: list[tuple[float, float]] = []

    for position, coordinate in enumerate(value):
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
            raise ValueError(
                f"Invalid coordinate at position {position} for route "
                f"{from_id} -> {to_id}"
            )

        x = _required_finite_float(
            coordinate[0],
            f"x coordinate {position} for route {from_id} -> {to_id}",
        )
        y = _required_finite_float(
            coordinate[1],
            f"y coordinate {position} for route {from_id} -> {to_id}",
        )

        coordinates.append((x, y))

    return coordinates


def _clean_id(value: Any) -> str:
    """Normalise Excel and route-record node IDs."""
    if value is None or isinstance(value, bool):
        raise ValueError("Node IDs cannot be blank or Boolean")

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Node IDs cannot be non-finite")
        if value.is_integer():
            return str(int(value))

    node_id = str(value).strip()

    if not node_id:
        raise ValueError("Node IDs cannot be blank")

    return node_id


def _required_finite_float(value: Any, label: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")

    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc

    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")

    return result


def _required_nonnegative_float(value: Any, label: str) -> float:
    result = _required_finite_float(value, label)

    if result < 0:
        raise ValueError(f"{label} cannot be negative")

    return result


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None

    result = float(value)
    return result if math.isfinite(result) else None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None

    return int(value)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None

    result = str(value).strip()
    return result or None


def _validate_output_paths(
    input_workbook: Path,
    workbook_output: Path,
    routes_output: Path,
) -> None:
    if input_workbook.resolve() == workbook_output.resolve():
        raise ValueError(
            "The routed workbook output must not overwrite the input workbook"
        )

    if workbook_output.resolve() == routes_output.resolve():
        raise ValueError(
            "The workbook and route GIS outputs must have different paths"
        )

    if workbook_output.suffix.lower() != ".xlsx":
        raise ValueError(
            "output_workbook_name must use the .xlsx extension"
        )

    if routes_output.suffix.lower() != ".gpkg":
        raise ValueError(
            "routes_filename must use the .gpkg extension"
        )


def _temporary_path(target: Path) -> Path:
    """Create a unique temporary output path in the target directory."""
    return target.with_name(
        f".{target.stem}-{uuid4().hex}{target.suffix}"
    )