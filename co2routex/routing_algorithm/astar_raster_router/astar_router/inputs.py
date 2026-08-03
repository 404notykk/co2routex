"""Read and validate the XLSX network input workbook."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .models import Connection, Node, Settings


REQUIRED_NODE_COLUMNS = {
    "node_id",
    "node_name",
    "longitude",
    "latitude",
    "altitude",
    "annual_flux",
    "node_type",
    "country_code",
}

DEFAULT_ALTITUDE_M = 10.0


def _clean_id(value: Any) -> str:
    """Convert a node identifier into a consistent non-empty string."""
    if pd.isna(value):
        raise ValueError("Node IDs cannot be blank")

    if isinstance(value, (float, np.floating)):
        numeric_value = float(value)

        if math.isfinite(numeric_value) and numeric_value.is_integer():
            cleaned = str(int(numeric_value))
        else:
            cleaned = str(value).strip()
    elif isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        cleaned = str(int(value))
    else:
        cleaned = str(value).strip()

    if not cleaned:
        raise ValueError("Node IDs cannot be blank")

    return cleaned


def _optional_text(value: Any) -> str:
    """Return a stripped string or an empty string for a blank cell."""
    if pd.isna(value):
        return ""

    return str(value).strip()


def _numeric_value(
    value: Any,
    field_name: str,
    node_id: str,
    *,
    default: float | None = None,
    non_negative: bool = False,
) -> float | None:
    """Read and validate an optional numeric node attribute."""
    if pd.isna(value) or (
        isinstance(value, str) and not value.strip()
    ):
        return default

    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Invalid {field_name} for node {node_id}: {value!r}"
        ) from error

    if not math.isfinite(result):
        raise ValueError(
            f"{field_name} must be finite for node {node_id}: {value!r}"
        )

    if non_negative and result < 0:
        raise ValueError(
            f"{field_name} cannot be negative for node {node_id}: "
            f"{result}"
        )

    return result


def _clean_attribute(value: Any) -> Any:
    """Convert a spreadsheet value into a serialisable Python value."""
    if pd.isna(value):
        return None

    if isinstance(value, np.generic):
        return value.item()

    return value


def load_network(
    settings: Settings,
) -> tuple[dict[str, Node], list[Connection]]:
    """Load nodes and permitted connections from an XLSX workbook."""
    if settings.workbook_path is None:
        raise ValueError(
            "workbook_path is required; CSV input is not supported"
        )

    workbook_path = settings.workbook_path

    if workbook_path.suffix.lower() != ".xlsx":
        raise ValueError(
            f"Routing input must be an .xlsx workbook: {workbook_path}"
        )

    modes = [str(mode).strip() for mode in settings.mode_sheets]

    if not modes or any(not mode for mode in modes):
        raise ValueError(
            "At least one valid matrix sheet must be configured"
        )

    if len(modes) != len(set(modes)):
        raise ValueError(
            "Matrix sheet names must not contain duplicates"
        )

    with pd.ExcelFile(workbook_path) as workbook:
        required_sheets = {"nodes", *modes}
        missing_sheets = required_sheets - set(workbook.sheet_names)

        if missing_sheets:
            raise ValueError(
                "Input workbook is missing required worksheets: "
                f"{sorted(missing_sheets)}"
            )

        nodes_frame = pd.read_excel(
            workbook,
            sheet_name="nodes",
        )

        matrices = {
            mode: pd.read_excel(
                workbook,
                sheet_name=mode,
                index_col=0,
            )
            for mode in modes
        }

    nodes = _parse_nodes(nodes_frame)
    node_ids = set(nodes)

    connections: list[Connection] = []

    for mode, matrix in matrices.items():
        connections.extend(
            _parse_matrix(
                mode,
                matrix,
                node_ids,
            )
        )

    return nodes, connections


def _parse_nodes(frame: pd.DataFrame) -> dict[str, Node]:
    """Parse and validate the nodes worksheet."""
    frame = frame.copy()
    frame.columns = [
        str(column).strip()
        for column in frame.columns
    ]

    if frame.columns.duplicated().any():
        duplicates = sorted(
            set(
                frame.columns[
                    frame.columns.duplicated()
                ].tolist()
            )
        )
        raise ValueError(
            f"nodes table has duplicate columns: {duplicates}"
        )

    missing_columns = REQUIRED_NODE_COLUMNS - set(frame.columns)

    if missing_columns:
        raise ValueError(
            "nodes table is missing required columns: "
            f"{sorted(missing_columns)}"
        )

    frame = frame.dropna(how="all")

    nodes: dict[str, Node] = {}

    for row_number, row in frame.iterrows():
        try:
            node_id = _clean_id(row["node_id"])
        except ValueError as error:
            excel_row = row_number + 2
            raise ValueError(
                f"Invalid node ID in nodes worksheet row {excel_row}"
            ) from error

        if node_id in nodes:
            raise ValueError(
                f"Duplicate node_id in nodes worksheet: {node_id}"
            )

        longitude = _numeric_value(
            row["longitude"],
            "longitude",
            node_id,
        )
        latitude = _numeric_value(
            row["latitude"],
            "latitude",
            node_id,
        )

        if longitude is None or latitude is None:
            raise ValueError(
                f"Node {node_id} must have longitude and latitude"
            )

        if not -180.0 <= longitude <= 180.0:
            raise ValueError(
                f"Longitude must be between -180 and 180 for "
                f"node {node_id}: {longitude}"
            )

        if not -90.0 <= latitude <= 90.0:
            raise ValueError(
                f"Latitude must be between -90 and 90 for "
                f"node {node_id}: {latitude}"
            )

        altitude = _numeric_value(
            row["altitude"],
            "altitude",
            node_id,
            default=DEFAULT_ALTITUDE_M,
        )

        annual_flux = _numeric_value(
            row["annual_flux"],
            "annual_flux",
            node_id,
            default=None,
            non_negative=True,
        )

        node_name = _optional_text(row["node_name"])
        node_type = _optional_text(row["node_type"])
        country_code = _optional_text(
            row["country_code"]
        ).upper()

        attributes = {
            str(key): _clean_attribute(value)
            for key, value in row.items()
            if key
            not in {
                "node_id",
                "node_name",
                "longitude",
                "latitude",
                "altitude",
                "annual_flux",
                "node_type",
                "country_code",
            }
        }

        attributes.update(
            {
                "altitude": altitude,
                "annual_flux": annual_flux,
                "node_type": node_type or None,
                "country_code": country_code or None,
            }
        )

        nodes[node_id] = Node(
            node_id=node_id,
            node_name=node_name,
            longitude=longitude,
            latitude=latitude,
            attributes=attributes,
        )

    if not nodes:
        raise ValueError(
            "nodes worksheet contains no node records"
        )

    return nodes


def _parse_matrix(
    mode: str,
    frame: pd.DataFrame,
    node_ids: set[str],
) -> list[Connection]:
    """Parse a directed connection matrix.

    Zero means that a connection is not permitted. Every positive value
    represents a permitted connection. Positive values may initially be
    permission flags such as 1 or previously calculated distances in
    kilometres.
    """
    frame = (
        frame
        .dropna(how="all")
        .dropna(axis=1, how="all")
        .copy()
    )

    if frame.empty:
        raise ValueError(
            f"{mode} matrix contains no data"
        )

    frame.index = [
        _clean_id(value)
        for value in frame.index
    ]
    frame.columns = [
        _clean_id(value)
        for value in frame.columns
    ]

    duplicate_rows = frame.index[
        frame.index.duplicated()
    ].tolist()
    duplicate_columns = frame.columns[
        frame.columns.duplicated()
    ].tolist()

    if duplicate_rows or duplicate_columns:
        raise ValueError(
            f"{mode} matrix contains duplicate identifiers; "
            f"duplicate rows={sorted(set(duplicate_rows))}, "
            f"duplicate columns={sorted(set(duplicate_columns))}"
        )

    matrix_rows = set(frame.index)
    matrix_columns = set(frame.columns)

    unknown_ids = (
        matrix_rows | matrix_columns
    ) - node_ids

    if unknown_ids:
        raise ValueError(
            f"{mode} matrix references unknown node IDs: "
            f"{sorted(unknown_ids)}"
        )

    missing_rows = node_ids - matrix_rows
    missing_columns = node_ids - matrix_columns

    if missing_rows or missing_columns:
        raise ValueError(
            f"{mode} matrix must contain every node ID as both "
            f"a row and a column; "
            f"missing rows={sorted(missing_rows)}, "
            f"missing columns={sorted(missing_columns)}"
        )

    numeric = frame.apply(
        pd.to_numeric,
        errors="coerce",
    )

    invalid_text = numeric.isna() & ~frame.isna()

    if invalid_text.to_numpy().any():
        invalid_cells: list[str] = []

        for row_id, column_id in zip(
            *np.where(invalid_text.to_numpy())
        ):
            from_id = frame.index[row_id]
            to_id = frame.columns[column_id]
            value = frame.iloc[row_id, column_id]

            invalid_cells.append(
                f"{from_id}→{to_id}={value!r}"
            )

            if len(invalid_cells) == 5:
                break

        raise ValueError(
            f"{mode} matrix contains nonnumeric values: "
            f"{', '.join(invalid_cells)}"
        )

    numeric = numeric.fillna(0.0).astype(float)

    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError(
            f"{mode} matrix must contain only finite numeric values"
        )

    negative_values = numeric < 0

    if negative_values.to_numpy().any():
        raise ValueError(
            f"{mode} matrix cannot contain negative values"
        )

    nonzero_diagonal = [
        node_id
        for node_id in node_ids
        if float(numeric.loc[node_id, node_id]) != 0.0
    ]

    if nonzero_diagonal:
        raise ValueError(
            f"{mode} matrix diagonal must be zero; "
            f"non-zero self-connections found for: "
            f"{sorted(nonzero_diagonal)}"
        )

    return [
        Connection(
            mode=mode,
            from_id=from_id,
            to_id=to_id,
        )
        for from_id in numeric.index
        for to_id in numeric.columns
        if (
            from_id != to_id
            and float(
                numeric.loc[from_id, to_id]
            ) > 0.0
        )
    ]