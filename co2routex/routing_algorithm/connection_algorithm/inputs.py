"""Read and validate nodes from the connection input workbook."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .models import Node


REQUIRED_NODE_COLUMNS = {
    "node_id",
    "longitude",
    "latitude",
    "node_type",
}


def load_nodes(workbook_path: Path, nodes_sheet: str) -> dict[str, Node]:
    """Load the configured nodes worksheet from an XLSX workbook."""
    if not workbook_path.exists():
        raise FileNotFoundError(
            f"Input workbook does not exist: {workbook_path}"
        )

    if not workbook_path.is_file():
        raise ValueError(f"Workbook path is not a file: {workbook_path}")

    if workbook_path.suffix.lower() != ".xlsx":
        raise ValueError("Input workbook must use the .xlsx extension")

    with pd.ExcelFile(workbook_path) as workbook:
        if nodes_sheet not in workbook.sheet_names:
            raise ValueError(
                f"Input workbook is missing the '{nodes_sheet}' worksheet"
            )

        frame = pd.read_excel(workbook, sheet_name=nodes_sheet)

    return parse_nodes(frame, nodes_sheet=nodes_sheet)


def parse_nodes(
    frame: pd.DataFrame,
    *,
    nodes_sheet: str = "nodes",
) -> dict[str, Node]:
    """Parse and validate a nodes dataframe."""
    frame = frame.copy()
    frame.columns = [str(column).strip() for column in frame.columns]

    if frame.columns.duplicated().any():
        duplicates = sorted(
            set(frame.columns[frame.columns.duplicated()].tolist())
        )
        raise ValueError(
            f"{nodes_sheet} worksheet has duplicate columns: {duplicates}"
        )

    missing_columns = REQUIRED_NODE_COLUMNS - set(frame.columns)

    if missing_columns:
        raise ValueError(
            f"{nodes_sheet} worksheet is missing required columns: "
            f"{sorted(missing_columns)}"
        )

    frame = frame.dropna(how="all")
    nodes: dict[str, Node] = {}

    for row_number, row in frame.iterrows():
        excel_row = int(row_number) + 2
        node_id = clean_id(row["node_id"])

        if node_id in nodes:
            raise ValueError(
                f"Duplicate node_id in {nodes_sheet} worksheet: {node_id}"
            )

        longitude = numeric_value(
            row["longitude"],
            "longitude",
            node_id,
        )
        latitude = numeric_value(
            row["latitude"],
            "latitude",
            node_id,
        )
        node_type = optional_text(row["node_type"]).lower()

        if not node_type:
            raise ValueError(
                f"node_type cannot be blank for node {node_id} "
                f"in worksheet row {excel_row}"
            )

        node_name = (
            optional_text(row["node_name"])
            if "node_name" in frame.columns
            else ""
        )

        standard_columns = {
            "node_id",
            "node_name",
            "longitude",
            "latitude",
            "node_type",
        }
        additional_attributes = {
            str(key): clean_attribute(value)
            for key, value in row.items()
            if key not in standard_columns
        }

        nodes[node_id] = Node(
            node_id=node_id,
            node_name=node_name,
            longitude=longitude,
            latitude=latitude,
            node_type=node_type,
            additional_attributes=additional_attributes,
        )

    if not nodes:
        raise ValueError(
            f"{nodes_sheet} worksheet contains no node records"
        )

    return nodes


def clean_id(value: Any) -> str:
    """Convert a spreadsheet node identifier to a stable string."""
    if pd.isna(value):
        raise ValueError("Node IDs cannot be blank")

    if isinstance(value, (float, np.floating)):
        numeric = float(value)
        cleaned = str(int(numeric)) if numeric.is_integer() else str(value)
    elif isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        cleaned = str(int(value))
    else:
        cleaned = str(value)

    cleaned = cleaned.strip()

    if not cleaned:
        raise ValueError("Node IDs cannot be blank")

    return cleaned


def numeric_value(value: Any, field_name: str, node_id: str) -> float:
    """Read a required finite numeric value."""
    if pd.isna(value) or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"{field_name} is required for node {node_id}")

    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Invalid {field_name} for node {node_id}: {value!r}"
        ) from error

    if not math.isfinite(result):
        raise ValueError(
            f"{field_name} must be finite for node {node_id}"
        )

    return result


def optional_text(value: Any) -> str:
    """Return a stripped string or an empty string for a blank cell."""
    if pd.isna(value):
        return ""

    return str(value).strip()


def clean_attribute(value: Any) -> Any:
    """Convert a spreadsheet value into a serialisable Python value."""
    if pd.isna(value):
        return None

    if isinstance(value, np.generic):
        return value.item()

    return value
