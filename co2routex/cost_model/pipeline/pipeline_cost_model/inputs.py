"""Read successful pipeline routes from the routed node workbook."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from .models import RouteInput


REQUIRED_COLUMNS = {
    "from_id",
    "to_id",
    "distance_km",
    "average_route_resistance",
}


def read_routes(workbook_path: Path, sheet_name: str) -> list[RouteInput]:
    frame = pd.read_excel(workbook_path, sheet_name=sheet_name, dtype={"from_id": str, "to_id": str})
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(
            f"Sheet {sheet_name!r} is missing columns: {', '.join(missing)}"
        )

    if "status" in frame.columns:
        frame = frame[frame["status"].astype(str).str.lower() == "ok"]
    if "mode" in frame.columns:
        frame = frame[frame["mode"].astype(str).str.lower() == "pipeline"]

    routes: list[RouteInput] = []
    seen: set[tuple[str, str]] = set()
    for row_number, row in frame.iterrows():
        from_id = _clean_id(row["from_id"])
        to_id = _clean_id(row["to_id"])
        key = (from_id, to_id)
        if key in seen:
            raise ValueError(f"Duplicate route {from_id} -> {to_id}")

        distance = _positive_float(row["distance_km"], "distance_km", row_number)
        resistance = _positive_float(
            row["average_route_resistance"],
            "average_route_resistance",
            row_number,
        )
        routes.append(RouteInput(from_id, to_id, distance, resistance))
        seen.add(key)

    if not routes:
        raise ValueError(f"No successful pipeline routes found in {sheet_name!r}")
    return routes


def _clean_id(value) -> str:
    if pd.isna(value):
        raise ValueError("Route node IDs cannot be blank")
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if not text:
        raise ValueError("Route node IDs cannot be blank")
    return text


def _positive_float(value, name: str, row_number: int) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {name} at Excel row {row_number + 2}") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be positive at Excel row {row_number + 2}")
    return number
