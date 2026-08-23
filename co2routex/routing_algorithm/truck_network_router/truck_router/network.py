from __future__ import annotations

import re
from collections.abc import Iterable
from numbers import Integral
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString, MultiLineString, Point
from shapely.strtree import STRtree

from .models import Segment, SnapResult


_OTHER_TAG_PATTERN = re.compile(r'"?([^"=,]+)"?\s*=>\s*"([^"]*)"')


def _clean_tag(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    return text


def _other_tags(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    text = _clean_tag(value)
    if not text:
        return {}
    return {key.strip(): val for key, val in _OTHER_TAG_PATTERN.findall(text)}


def _row_tag(row: Any, name: str) -> str | None:
    lowered = {str(column).lower(): column for column in row.index}
    column = lowered.get(name.lower())
    if column is not None:
        value = _clean_tag(row[column])
        if value is not None:
            return value
    other_column = lowered.get("other_tags")
    if other_column is not None:
        return _clean_tag(_other_tags(row[other_column]).get(name))
    return None


def _direction_flags(row: Any, respect_oneway: bool) -> tuple[bool, bool]:
    if not respect_oneway:
        return True, True

    highway = (_row_tag(row, "highway") or "").lower()
    oneway = (_row_tag(row, "oneway") or "").lower()
    junction = (_row_tag(row, "junction") or "").lower()

    if oneway in {"-1", "reverse"}:
        return False, True
    if oneway in {"yes", "1", "true"}:
        return True, False
    if oneway in {"no", "0", "false"}:
        return True, True
    if junction == "roundabout":
        return True, False
    if highway in {"motorway", "motorway_link"}:
        return True, False
    return True, True


def _parts(geometry: Any) -> Iterable[LineString]:
    if isinstance(geometry, LineString):
        yield geometry
    elif isinstance(geometry, MultiLineString):
        yield from geometry.geoms


def _node_key(coordinate: tuple[float, float]) -> tuple[float, float]:
    return round(float(coordinate[0]), 3), round(float(coordinate[1]), 3)


class RoadNetwork:
    def __init__(
        self,
        graph: nx.MultiDiGraph,
        segments: list[Segment],
        crs: str,
    ) -> None:
        if not segments:
            raise ValueError("The road network contains no usable line segments")
        self.graph = graph
        self.segments = segments
        self.crs = crs
        self._geometries = [segment.geometry for segment in segments]
        self._tree = STRtree(self._geometries)
        self._geometry_index = {
            geometry.wkb: index for index, geometry in enumerate(self._geometries)
        }

    def snap(self, point: Point) -> SnapResult:
        nearest = self._tree.nearest(point)
        if isinstance(nearest, Integral):
            index = int(nearest)
        else:
            index = self._geometry_index[nearest.wkb]
        segment = self.segments[index]
        position = float(segment.geometry.project(point))
        snapped = segment.geometry.interpolate(position)
        return SnapResult(
            point=snapped,
            segment=segment,
            position_m=position,
            distance_m=float(point.distance(snapped)),
        )


def load_road_network(
    path: str | Path,
    layer: str,
    working_crs: str,
    respect_oneway: bool,
) -> RoadNetwork:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Truck network not found: {path}")

    roads = gpd.read_file(path, layer=layer)
    if roads.empty:
        raise ValueError(f"Truck network layer is empty: {path}:{layer}")
    if roads.crs is None:
        raise ValueError(f"Truck network has no CRS: {path}:{layer}")

    roads = roads.to_crs(working_crs)
    graph = nx.MultiDiGraph()
    segments: list[Segment] = []

    for _, row in roads.iterrows():
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            continue
        forward, reverse = _direction_flags(row, respect_oneway)
        highway = _row_tag(row, "highway")

        for part in _parts(geometry):
            coordinates = list(part.coords)
            for start, end in zip(coordinates, coordinates[1:]):
                u = _node_key(start)
                v = _node_key(end)
                if u == v:
                    continue
                line = LineString([u, v])
                length_m = float(line.length)
                if length_m <= 0:
                    continue

                segment_id = len(segments)
                segment = Segment(
                    segment_id=segment_id,
                    u=u,
                    v=v,
                    geometry=line,
                    length_m=length_m,
                    forward=forward,
                    reverse=reverse,
                    highway=highway,
                )
                segments.append(segment)
                graph.add_node(u, x=u[0], y=u[1])
                graph.add_node(v, x=v[0], y=v[1])

                if forward:
                    graph.add_edge(
                        u,
                        v,
                        key=f"{segment_id}:f",
                        length_m=length_m,
                        geometry=line,
                        segment_id=segment_id,
                    )
                if reverse:
                    graph.add_edge(
                        v,
                        u,
                        key=f"{segment_id}:r",
                        length_m=length_m,
                        geometry=LineString(list(line.coords)[::-1]),
                        segment_id=segment_id,
                    )

    return RoadNetwork(graph=graph, segments=segments, crs=working_crs)
