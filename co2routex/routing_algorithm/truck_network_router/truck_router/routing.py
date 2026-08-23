from __future__ import annotations

from uuid import uuid4

import networkx as nx
from shapely.geometry import LineString
from shapely.ops import substring

from .models import SnapResult
from .network import RoadNetwork


def _line_piece(line: LineString, start: float, end: float) -> LineString:
    if abs(start - end) < 1e-9:
        point = line.interpolate(start)
        return LineString([point.coords[0], point.coords[0]])
    piece = substring(line, start, end)
    if isinstance(piece, LineString):
        return piece
    return LineString([piece.coords[0], piece.coords[0]])


def _add_edge(
    graph: nx.MultiDiGraph,
    u: object,
    v: object,
    length_m: float,
    geometry: LineString,
) -> None:
    graph.add_edge(
        u,
        v,
        key=f"virtual:{uuid4().hex}",
        length_m=max(0.0, float(length_m)),
        geometry=geometry,
        virtual=True,
    )


def _best_edge(graph: nx.MultiDiGraph, u: object, v: object) -> dict:
    edges = graph.get_edge_data(u, v)
    if not edges:
        raise nx.NetworkXNoPath(f"No edge between path nodes {u!r} and {v!r}")
    return min(edges.values(), key=lambda data: float(data["length_m"]))


def _merge_lines(lines: list[LineString]) -> LineString:
    coordinates: list[tuple[float, float]] = []
    for line in lines:
        part = list(line.coords)
        if not part:
            continue
        if coordinates and coordinates[-1] == part[0]:
            coordinates.extend(part[1:])
        else:
            coordinates.extend(part)
    if len(coordinates) == 1:
        coordinates.append(coordinates[0])
    return LineString(coordinates)


def shortest_route(
    network: RoadNetwork,
    start: SnapResult,
    end: SnapResult,
) -> tuple[float, LineString]:
    graph = network.graph
    start_node = ("__truck_start__", uuid4().hex)
    end_node = ("__truck_end__", uuid4().hex)
    graph.add_node(start_node)
    graph.add_node(end_node)

    try:
        start_segment = start.segment
        end_segment = end.segment

        if start_segment.forward:
            _add_edge(
                graph,
                start_node,
                start_segment.v,
                start_segment.length_m - start.position_m,
                _line_piece(
                    start_segment.geometry,
                    start.position_m,
                    start_segment.length_m,
                ),
            )
        if start_segment.reverse:
            _add_edge(
                graph,
                start_node,
                start_segment.u,
                start.position_m,
                _line_piece(start_segment.geometry, start.position_m, 0.0),
            )

        if end_segment.forward:
            _add_edge(
                graph,
                end_segment.u,
                end_node,
                end.position_m,
                _line_piece(end_segment.geometry, 0.0, end.position_m),
            )
        if end_segment.reverse:
            _add_edge(
                graph,
                end_segment.v,
                end_node,
                end_segment.length_m - end.position_m,
                _line_piece(
                    end_segment.geometry,
                    end_segment.length_m,
                    end.position_m,
                ),
            )

        if start_segment.segment_id == end_segment.segment_id:
            difference = end.position_m - start.position_m
            if difference >= 0 and start_segment.forward:
                _add_edge(
                    graph,
                    start_node,
                    end_node,
                    difference,
                    _line_piece(
                        start_segment.geometry,
                        start.position_m,
                        end.position_m,
                    ),
                )
            if difference <= 0 and start_segment.reverse:
                _add_edge(
                    graph,
                    start_node,
                    end_node,
                    abs(difference),
                    _line_piece(
                        start_segment.geometry,
                        start.position_m,
                        end.position_m,
                    ),
                )

        path = nx.shortest_path(
            graph,
            source=start_node,
            target=end_node,
            weight="length_m",
            method="dijkstra",
        )

        edge_data = [
            _best_edge(graph, u, v) for u, v in zip(path, path[1:])
        ]
        length_m = sum(float(data["length_m"]) for data in edge_data)
        geometry = _merge_lines([data["geometry"] for data in edge_data])
        return length_m, geometry
    finally:
        graph.remove_node(start_node)
        graph.remove_node(end_node)
