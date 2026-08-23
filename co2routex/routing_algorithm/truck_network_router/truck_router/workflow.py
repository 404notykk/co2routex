from __future__ import annotations

import logging

import networkx as nx
from pyproj import Transformer
from shapely.geometry import LineString, Point

from .eligibility import is_eligible_pair
from .input import load_existing_nonzero_pairs, load_nodes
from .models import Node, RouteResult, Settings, SnapResult
from .network import RoadNetwork, load_road_network
from .outputs import update_truck_sheet, write_routes, write_summary
from .routing import shortest_route


LOGGER = logging.getLogger(__name__)


def _full_geometry(
    origin: Point,
    network_geometry: LineString,
    destination: Point,
) -> LineString:
    coordinates = [origin.coords[0]]
    route_coordinates = list(network_geometry.coords)
    if coordinates[-1] != route_coordinates[0]:
        coordinates.append(route_coordinates[0])
    coordinates.extend(route_coordinates[1:])
    if coordinates[-1] != destination.coords[0]:
        coordinates.append(destination.coords[0])
    if len(coordinates) == 1:
        coordinates.append(coordinates[0])
    return LineString(coordinates)


def run(settings: Settings) -> dict[str, object]:
    nodes = load_nodes(settings)
    existing_pairs = (
        load_existing_nonzero_pairs(settings)
        if settings.candidate_source == "existing_nonzero"
        else None
    )

    transformer = Transformer.from_crs(
        settings.nodes_crs,
        settings.working_crs,
        always_xy=True,
    )
    points = {
        str(node.node_id): Point(
            *transformer.transform(node.longitude, node.latitude)
        )
        for node in nodes
    }

    networks: dict[str, RoadNetwork] = {}
    snap_cache: dict[tuple[str, str], SnapResult] = {}
    results: list[RouteResult] = []

    def get_network(country: str) -> RoadNetwork:
        if country not in settings.networks:
            raise KeyError(f"No truck network configured for country {country}")
        if country not in networks:
            source = settings.networks[country]
            LOGGER.info("Loading %s truck network from %s", country, source.path)
            networks[country] = load_road_network(
                path=source.path,
                layer=source.layer,
                working_crs=settings.working_crs,
                respect_oneway=settings.respect_oneway,
            )
        return networks[country]

    def get_snap(node: Node, network: RoadNetwork) -> SnapResult:
        key = (node.country_code, str(node.node_id))
        if key not in snap_cache:
            snap_cache[key] = network.snap(points[str(node.node_id)])
        return snap_cache[key]

    for origin in nodes:
        for destination in nodes:
            if not is_eligible_pair(
                origin,
                destination,
                policy=settings.connection_policy,
                existing_nonzero_pairs=existing_pairs,
            ):
                continue

            base = dict(
                from_id=origin.node_id,
                to_id=destination.node_id,
                from_name=origin.node_name,
                to_name=destination.node_name,
                country_code=origin.country_code,
            )
            try:
                network = get_network(origin.country_code)
                start = get_snap(origin, network)
                end = get_snap(destination, network)

                if start.distance_m > settings.maximum_snap_distance_m:
                    results.append(
                        RouteResult(
                            **base,
                            status="snap_too_far",
                            message=(
                                f"Origin is {start.distance_m:.1f} m from the "
                                "truck network"
                            ),
                            from_snap_distance_m=start.distance_m,
                            to_snap_distance_m=end.distance_m,
                        )
                    )
                    continue
                if end.distance_m > settings.maximum_snap_distance_m:
                    results.append(
                        RouteResult(
                            **base,
                            status="snap_too_far",
                            message=(
                                f"Destination is {end.distance_m:.1f} m from "
                                "the truck network"
                            ),
                            from_snap_distance_m=start.distance_m,
                            to_snap_distance_m=end.distance_m,
                        )
                    )
                    continue

                network_length_m, network_geometry = shortest_route(
                    network, start, end
                )
                access_length_m = start.distance_m + end.distance_m
                metric_distance_m = network_length_m + (
                    access_length_m
                    if settings.include_snap_distance_in_metric
                    else 0.0
                )
                geometry = _full_geometry(
                    points[str(origin.node_id)],
                    network_geometry,
                    points[str(destination.node_id)],
                )
                results.append(
                    RouteResult(
                        **base,
                        status="success",
                        message="Route found",
                        network_length_m=network_length_m,
                        metric_distance_m=metric_distance_m,
                        from_snap_distance_m=start.distance_m,
                        to_snap_distance_m=end.distance_m,
                        geometry=geometry,
                    )
                )
            except (FileNotFoundError, KeyError, ValueError) as exc:
                results.append(
                    RouteResult(
                        **base,
                        status="network_error",
                        message=str(exc),
                    )
                )
            except nx.NetworkXNoPath:
                results.append(
                    RouteResult(
                        **base,
                        status="no_path",
                        message="No directed road path connects the snapped points",
                    )
                )

    workbook_path = update_truck_sheet(settings, nodes, results)
    summary_path = write_summary(settings, results)
    routes_path = write_routes(settings, results)
    return {
        "workbook_path": workbook_path,
        "summary_path": summary_path,
        "routes_path": routes_path,
        "results": results,
    }
