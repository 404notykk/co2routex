from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.strtree import STRtree

from .models import DistanceCalculationSettings


def _read_vector(path: Path, layer: str | None) -> gpd.GeoDataFrame:
    kwargs = {"layer": layer} if layer else {}
    return gpd.read_file(path, **kwargs)


def _line_parts(geometry) -> Iterable[LineString]:
    if isinstance(geometry, LineString):
        yield geometry
    elif isinstance(geometry, MultiLineString):
        yield from geometry.geoms


def _station_point(geometry) -> Point | None:
    if geometry is None or geometry.is_empty:
        return None
    if isinstance(geometry, Point):
        return geometry
    return geometry.representative_point()


def _node_key(point: Point) -> str:
    return f"{point.x:.3f},{point.y:.3f}"


def _network_segments(railway: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    rows: list[dict] = []
    segment_id = 0
    for geometry in railway.geometry:
        for line in _line_parts(geometry):
            coordinates = list(line.coords)
            for start, end in zip(coordinates, coordinates[1:]):
                segment = LineString([start, end])
                if segment.length <= 0:
                    continue
                rows.append({"segment_id": segment_id, "geometry": segment})
                segment_id += 1
    if not rows:
        raise ValueError("The railway GeoPackage contains no usable line geometry.")
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=railway.crs)


def _prepare_stations(
    stations: gpd.GeoDataFrame,
    station_id_field: str,
    station_name_field: str,
) -> gpd.GeoDataFrame:
    if station_id_field not in stations.columns:
        raise ValueError(f"Station layer is missing field '{station_id_field}'.")
    if station_name_field not in stations.columns:
        raise ValueError(f"Station layer is missing field '{station_name_field}'.")

    rows: list[dict] = []
    for _, source in stations.iterrows():
        point = _station_point(source.geometry)
        if point is None:
            continue
        station_id = str(source[station_id_field]).strip()
        if not station_id:
            raise ValueError("Every pre-identified railway station requires a station ID.")
        rows.append(
            {
                "station_id": station_id,
                "station_name": str(source[station_name_field]),
                "geometry": point,
            }
        )
    prepared = gpd.GeoDataFrame(rows, geometry="geometry", crs=stations.crs)
    if len(prepared) < 2:
        raise ValueError("At least two pre-identified railway stations are required.")
    duplicates = prepared.loc[prepared["station_id"].duplicated(), "station_id"].tolist()
    if duplicates:
        raise ValueError(f"Duplicate station IDs: {duplicates}")
    return prepared


def _snap_stations_and_build_graph(
    segments: gpd.GeoDataFrame,
    stations: gpd.GeoDataFrame,
) -> tuple[nx.Graph, gpd.GeoDataFrame]:
    segment_geometries = list(segments.geometry)
    tree = STRtree(segment_geometries)
    split_points: dict[int, list[tuple[float, str, Point]]] = {}
    station_rows: list[dict] = []

    for station in stations.itertuples(index=False):
        segment_index = int(tree.nearest(station.geometry))
        segment = segment_geometries[segment_index]
        offset = float(segment.project(station.geometry))
        snapped_point = segment.interpolate(offset)
        graph_node = _node_key(snapped_point)
        split_points.setdefault(segment_index, []).append(
            (offset, station.station_id, snapped_point)
        )
        station_rows.append(
            {
                "station_id": station.station_id,
                "station_name": station.station_name,
                "graph_node": graph_node,
                "snap_distance_m": float(station.geometry.distance(snapped_point)),
                "geometry": station.geometry,
            }
        )

    snapped_stations = gpd.GeoDataFrame(
        station_rows, geometry="geometry", crs=stations.crs
    )
    duplicate_nodes = snapped_stations.loc[
        snapped_stations["graph_node"].duplicated(keep=False),
        ["station_id", "graph_node"],
    ]
    if not duplicate_nodes.empty:
        raise ValueError(
            "Multiple stations snapped to the same railway graph location: "
            f"{duplicate_nodes.to_dict(orient='records')}"
        )

    graph = nx.Graph()
    for segment_index, segment in enumerate(segment_geometries):
        points: list[tuple[float, Point]] = [
            (0.0, Point(segment.coords[0])),
            (float(segment.length), Point(segment.coords[-1])),
        ]
        points.extend(
            (offset, point)
            for offset, _, point in split_points.get(segment_index, [])
        )
        ordered: list[tuple[float, Point]] = []
        seen: set[str] = set()
        for offset, point in sorted(points, key=lambda item: item[0]):
            key = _node_key(point)
            if key not in seen:
                ordered.append((offset, point))
                seen.add(key)
        for (_, start), (_, end) in zip(ordered, ordered[1:]):
            u = _node_key(start)
            v = _node_key(end)
            length_m = float(start.distance(end))
            if length_m <= 0:
                continue
            graph.add_node(u, x=float(start.x), y=float(start.y))
            graph.add_node(v, x=float(end.x), y=float(end.y))
            existing = graph.get_edge_data(u, v)
            if existing is None or length_m < existing["length_m"]:
                graph.add_edge(u, v, length_m=length_m)
    return graph, snapped_stations


def _calculate_routes(
    stations: gpd.GeoDataFrame,
    graph: nx.Graph,
) -> gpd.GeoDataFrame:
    records: list[dict] = []
    station_records = list(
        stations[["station_id", "station_name", "graph_node"]].itertuples(
            index=False, name=None
        )
    )
    for index, (from_id, from_name, from_node) in enumerate(station_records):
        lengths, paths = nx.single_source_dijkstra(graph, from_node, weight="length_m")
        for to_id, to_name, to_node in station_records[index + 1 :]:
            distance_m = lengths.get(to_node)
            if distance_m is None or not math.isfinite(distance_m):
                continue
            path_nodes = paths[to_node]
            geometry = LineString(
                [(graph.nodes[node]["x"], graph.nodes[node]["y"]) for node in path_nodes]
            )
            records.append(
                {
                    "from_station_id": from_id,
                    "to_station_id": to_id,
                    "from_station_name": from_name,
                    "to_station_name": to_name,
                    "distance_km": float(distance_m) / 1000.0,
                    "geometry": geometry,
                }
            )
    if not records:
        raise ValueError("No connected station pairs were found on the railway network.")
    return gpd.GeoDataFrame(records, geometry="geometry", crs=stations.crs)


def calculate_station_distances(settings: DistanceCalculationSettings) -> dict:
    for path, label in (
        (settings.railway_path, "Railway GeoPackage"),
        (settings.stations_path, "Railway stations GeoPackage"),
    ):
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path}")
    if settings.output_routes_path.resolve() in {
        settings.railway_path.resolve(),
        settings.stations_path.resolve(),
    }:
        raise ValueError("output_routes_path must not overwrite an input GeoPackage.")

    railway = _read_vector(settings.railway_path, settings.railway_layer)
    stations = _read_vector(settings.stations_path, settings.stations_layer)
    if railway.crs is None or stations.crs is None:
        raise ValueError("Railway and station layers must both have a defined CRS.")
    railway = railway.to_crs(settings.routing_crs)
    stations = stations.to_crs(settings.routing_crs)
    railway = railway[railway.geometry.notna() & ~railway.geometry.is_empty].copy()
    stations = stations[stations.geometry.notna() & ~stations.geometry.is_empty].copy()

    prepared_stations = _prepare_stations(
        stations,
        settings.station_id_field,
        settings.station_name_field,
    )
    segments = _network_segments(railway)
    graph, snapped_stations = _snap_stations_and_build_graph(segments, prepared_stations)
    routes = _calculate_routes(snapped_stations, graph)

    settings.output_routes_path.parent.mkdir(parents=True, exist_ok=True)
    settings.output_distances_path.parent.mkdir(parents=True, exist_ok=True)
    routes.to_file(
        settings.output_routes_path,
        layer=settings.output_routes_layer,
        driver="GPKG",
    )
    pd.DataFrame(routes.drop(columns="geometry")).to_csv(
        settings.output_distances_path,
        index=False,
    )
    possible_pairs = len(snapped_stations) * (len(snapped_stations) - 1) // 2
    return {
        "country_code": settings.country_code,
        "station_count": len(snapped_stations),
        "connected_station_pairs": len(routes),
        "disconnected_station_pairs": possible_pairs - len(routes),
        "output_routes_path": settings.output_routes_path,
        "output_distances_path": settings.output_distances_path,
    }
