"""Coordinate network loading, raster routing and output generation."""

from __future__ import annotations

import logging
import time
from typing import Any

from .astar import NoPathError, astar_search
from .inputs import load_network
from .models import Connection, Node, SearchResult, Settings
from .outputs import write_outputs
from .raster import ResistanceRaster


LOGGER = logging.getLogger(__name__)


def run(settings: Settings) -> list[dict[str, Any]]:
    """
    Run least-resistance routing for every permitted pipeline connection.

    Parameters
    ----------
    settings
        Validated routing settings.

    Returns
    -------
    list[dict[str, Any]]
        One result record for every requested connection.
    """
    started = time.perf_counter()

    nodes, connections = load_network(settings)

    raster = ResistanceRaster(
        settings.raster_path,
        zero_is_barrier=settings.zero_is_barrier,
    )

    node_locations = raster.node_cells(
        nodes,
        settings.nodes_crs,
        settings.snap_radius_cells,
    )

    records: list[dict[str, Any]] = []

    route_cache: dict[
        tuple[tuple[int, int], tuple[int, int]],
        SearchResult,
    ] = {}

    total_connections = len(connections)

    for number, connection in enumerate(connections, start=1):
        LOGGER.info(
            "Routing %s/%s: %s %s -> %s",
            number,
            total_connections,
            connection.mode,
            connection.from_id,
            connection.to_id,
        )

        record = _route_connection(
            connection=connection,
            nodes=nodes,
            node_locations=node_locations,
            raster=raster,
            settings=settings,
            route_cache=route_cache,
        )
        records.append(record)

    workbook_path, routes_path = write_outputs(
        settings,
        records,
        raster.crs,
    )

    succeeded = sum(record["status"] == "ok" for record in records)
    elapsed_seconds = time.perf_counter() - started

    LOGGER.info(
        "Routing completed: %s/%s successful in %.2f seconds",
        succeeded,
        total_connections,
        elapsed_seconds,
    )
    LOGGER.info("Updated workbook: %s", workbook_path)
    LOGGER.info("Route GeoPackage: %s", routes_path)

    return records


def _route_connection(
    *,
    connection: Connection,
    nodes: dict[str, Node],
    node_locations: dict[str, dict[str, Any]],
    raster: ResistanceRaster,
    settings: Settings,
    route_cache: dict[
        tuple[tuple[int, int], tuple[int, int]],
        SearchResult,
    ],
) -> dict[str, Any]:
    """Route one permitted connection and construct its output record."""
    record = _base_record(connection, nodes, node_locations)

    start = node_locations[connection.from_id]["cell"]
    goal = node_locations[connection.to_id]["cell"]

    cache_key = (start, goal)
    reverse_cache_key = (goal, start)

    try:
        if cache_key in route_cache:
            result = route_cache[cache_key]

        elif (
            settings.cache_reverse_routes
            and reverse_cache_key in route_cache
        ):
            reverse_result = route_cache[reverse_cache_key]

            result = SearchResult(
                path=list(reversed(reverse_result.path)),
                accumulated_resistance=(
                    reverse_result.accumulated_resistance
                ),
                geometric_length_map_units=(
                    reverse_result.geometric_length_map_units
                ),
                explored_cells=reverse_result.explored_cells,
            )

            route_cache[cache_key] = result

        else:
            result = astar_search(
                resistance=raster.resistance,
                traversable=raster.traversable,
                transform=raster.transform,
                start=start,
                goal=goal,
                connectivity=settings.connectivity,
                prevent_corner_cutting=(
                    settings.prevent_corner_cutting
                ),
            )

            route_cache[cache_key] = result

        # ResistanceRaster requires a projected CRS measured in metres.
        distance_km = result.geometric_length_map_units / 1000.0

        record.update(
            {
                "status": "ok",
                "message": "",
                "distance_km": distance_km,
                "accumulated_resistance": (
                    result.accumulated_resistance
                ),
                "explored_cells": result.explored_cells,
                "path_cells": len(result.path),
                "coordinates": [
                    raster.cell_xy(cell)
                    for cell in result.path
                ],
            }
        )

    except (NoPathError, ValueError) as exc:
        LOGGER.warning(
            "Route %s -> %s failed: %s",
            connection.from_id,
            connection.to_id,
            exc,
        )

        record.update(
            {
                "status": "failed",
                "message": str(exc),
                "distance_km": None,
                "accumulated_resistance": None,
                "explored_cells": None,
                "path_cells": None,
                "coordinates": None,
            }
        )

    return record


def _base_record(
    connection: Connection,
    nodes: dict[str, Node],
    locations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Create attributes shared by successful and failed route records."""
    from_node = nodes[connection.from_id]
    to_node = nodes[connection.to_id]

    from_location = locations[connection.from_id]
    to_location = locations[connection.to_id]

    return {
        "mode": connection.mode,
        "from_id": connection.from_id,
        "to_id": connection.to_id,
        "from_name": from_node.node_name,
        "to_name": to_node.node_name,
        "from_longitude": from_node.longitude,
        "from_latitude": from_node.latitude,
        "to_longitude": to_node.longitude,
        "to_latitude": to_node.latitude,
        "from_snap_distance_m": from_location["snap_distance_m"],
        "to_snap_distance_m": to_location["snap_distance_m"],
    }