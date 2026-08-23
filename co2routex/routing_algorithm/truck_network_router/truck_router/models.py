from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point


@dataclass(frozen=True)
class Node:
    node_id: Any
    node_name: str
    longitude: float
    latitude: float
    altitude: float | None
    annual_flux: float | None
    node_type: str
    country_code: str


@dataclass(frozen=True)
class NetworkSource:
    path: Path
    layer: str = "truck_network"


@dataclass(frozen=True)
class Settings:
    config_path: Path
    workbook_path: Path
    output_workbook_path: Path
    nodes_sheet: str
    truck_sheet: str
    nodes_crs: str
    working_crs: str
    output_crs: str
    networks: dict[str, NetworkSource]
    maximum_snap_distance_m: float
    include_snap_distance_in_metric: bool
    respect_oneway: bool
    connection_policy: str
    candidate_source: str
    output_dir: Path
    routes_filename: str
    summary_filename: str


@dataclass(frozen=True)
class Segment:
    segment_id: int
    u: tuple[float, float]
    v: tuple[float, float]
    geometry: LineString
    length_m: float
    forward: bool
    reverse: bool
    highway: str | None = None


@dataclass(frozen=True)
class SnapResult:
    point: Point
    segment: Segment
    position_m: float
    distance_m: float


@dataclass
class RouteResult:
    from_id: Any
    to_id: Any
    from_name: str
    to_name: str
    country_code: str
    status: str
    message: str
    network_length_m: float | None = None
    metric_distance_m: float | None = None
    from_snap_distance_m: float | None = None
    to_snap_distance_m: float | None = None
    geometry: LineString | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
