from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DistanceCalculationSettings:
    country_code: str
    railway_path: Path
    stations_path: Path
    output_routes_path: Path
    output_distances_path: Path
    routing_crs: str
    railway_layer: str | None = None
    stations_layer: str | None = None
    output_routes_layer: str = "station_routes"
    station_id_field: str = "station_id"
    station_name_field: str = "station_name"


@dataclass(frozen=True)
class RailwayRequestSettings:
    requested: bool
    workbook_path: Path
    country_code: str
    stations_path: Path
    distances_path: Path
    nodes_crs: str = "EPSG:4326"
    stations_layer: str | None = None
    station_id_field: str = "station_id"
    station_name_field: str = "station_name"
    maximum_search_radius_km: float = 5.0
    station_node_type: str = "transport"
    station_altitude: float = 10.0
    matrix_sheets: tuple[str, ...] = ("pipeline", "truck", "railway")
    reset_railway_matrix: bool = True


@dataclass(frozen=True)
class Settings:
    distance_calculation: DistanceCalculationSettings | None = None
    railway_request: RailwayRequestSettings | None = None

