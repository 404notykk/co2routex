from __future__ import annotations

from typing import Any

import pandas as pd

from .models import Node, Settings


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


def normalize_id(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def id_key(value: Any) -> str:
    return str(normalize_id(value)).strip()


def _optional_number(value: Any) -> float | None:
    if pd.isna(value) or value == "":
        return None
    return float(value)


def load_nodes(settings: Settings) -> list[Node]:
    if not settings.workbook_path.exists():
        raise FileNotFoundError(
            f"Input workbook not found: {settings.workbook_path}"
        )

    frame = pd.read_excel(
        settings.workbook_path,
        sheet_name=settings.nodes_sheet,
    )
    frame.columns = [str(column).strip() for column in frame.columns]

    missing = sorted(REQUIRED_NODE_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError(
            f"Sheet {settings.nodes_sheet!r} is missing columns: {missing}"
        )

    nodes: list[Node] = []
    seen: set[str] = set()
    for row_number, row in frame.iterrows():
        if pd.isna(row["node_id"]):
            continue
        node_id = normalize_id(row["node_id"])
        key = id_key(node_id)
        if key in seen:
            raise ValueError(f"Duplicate node_id found: {node_id!r}")
        seen.add(key)

        country_code = str(row["country_code"]).strip().upper()
        if not country_code or country_code == "NAN":
            raise ValueError(
                f"Missing country_code for node {node_id!r} "
                f"(Excel row {row_number + 2})"
            )

        nodes.append(
            Node(
                node_id=node_id,
                node_name=str(row["node_name"]).strip(),
                longitude=float(row["longitude"]),
                latitude=float(row["latitude"]),
                altitude=_optional_number(row["altitude"]),
                annual_flux=_optional_number(row["annual_flux"]),
                node_type=str(row["node_type"]).strip(),
                country_code=country_code,
            )
        )

    if not nodes:
        raise ValueError(f"Sheet {settings.nodes_sheet!r} contains no nodes")
    return nodes


def load_existing_nonzero_pairs(
    settings: Settings,
) -> set[tuple[str, str]]:
    try:
        frame = pd.read_excel(
            settings.workbook_path,
            sheet_name=settings.truck_sheet,
            index_col=0,
        )
    except ValueError:
        return set()

    pairs: set[tuple[str, str]] = set()
    for from_id, row in frame.iterrows():
        for to_id, value in row.items():
            if pd.notna(value) and value != 0:
                pairs.add((id_key(from_id), id_key(to_id)))
    return pairs
