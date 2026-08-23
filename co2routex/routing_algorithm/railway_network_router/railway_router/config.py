from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import DistanceCalculationSettings, RailwayRequestSettings, Settings


def _resolve(base: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _required(data: dict[str, Any], key: str, section: str) -> Any:
    value = data.get(key)
    if value is None or value == "":
        raise ValueError(f"Missing required setting '{section}.{key}'.")
    return value


def _required_path(base: Path, data: dict[str, Any], key: str, section: str) -> Path:
    path = _resolve(base, str(_required(data, key, section)))
    assert path is not None
    return path


def load_settings(config_path: Path) -> Settings:
    config_path = config_path.expanduser().resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    base = config_path.parent

    distance_settings = None
    distance_raw = raw.get("distance_calculation")
    if distance_raw:
        distance_settings = DistanceCalculationSettings(
            country_code=str(
                _required(distance_raw, "country_code", "distance_calculation")
            ).upper(),
            railway_path=_required_path(
                base, distance_raw, "railway_path", "distance_calculation"
            ),
            stations_path=_required_path(
                base, distance_raw, "stations_path", "distance_calculation"
            ),
            output_routes_path=_required_path(
                base, distance_raw, "output_routes_path", "distance_calculation"
            ),
            output_distances_path=_required_path(
                base, distance_raw, "output_distances_path", "distance_calculation"
            ),
            routing_crs=str(distance_raw.get("routing_crs", "EPSG:3035")),
            railway_layer=distance_raw.get("railway_layer"),
            stations_layer=distance_raw.get("stations_layer"),
            output_routes_layer=str(
                distance_raw.get("output_routes_layer", "station_routes")
            ),
            station_id_field=str(distance_raw.get("station_id_field", "station_id")),
            station_name_field=str(
                distance_raw.get("station_name_field", "station_name")
            ),
        )

    request_settings = None
    request_raw = raw.get("railway_request")
    if request_raw:
        request_settings = RailwayRequestSettings(
            requested=bool(request_raw.get("requested", False)),
            workbook_path=_required_path(
                base, request_raw, "workbook_path", "railway_request"
            ),
            country_code=str(
                _required(request_raw, "country_code", "railway_request")
            ).upper(),
            stations_path=_required_path(
                base, request_raw, "stations_path", "railway_request"
            ),
            distances_path=_required_path(
                base, request_raw, "distances_path", "railway_request"
            ),
            nodes_crs=str(request_raw.get("nodes_crs", "EPSG:4326")),
            stations_layer=request_raw.get("stations_layer"),
            station_id_field=str(request_raw.get("station_id_field", "station_id")),
            station_name_field=str(
                request_raw.get("station_name_field", "station_name")
            ),
            maximum_search_radius_km=float(
                request_raw.get("maximum_search_radius_km", 5.0)
            ),
            station_node_type=str(request_raw.get("station_node_type", "transport")),
            station_altitude=float(request_raw.get("station_altitude", 10.0)),
            matrix_sheets=tuple(
                request_raw.get("matrix_sheets", ["pipeline", "truck", "railway"])
            ),
            reset_railway_matrix=bool(request_raw.get("reset_railway_matrix", True)),
        )

    if distance_settings is None and request_settings is None:
        raise ValueError(
            "The configuration must contain 'distance_calculation' or 'railway_request'."
        )
    return Settings(
        distance_calculation=distance_settings,
        railway_request=request_settings,
    )

