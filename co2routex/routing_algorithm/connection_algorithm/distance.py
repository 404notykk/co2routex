"""Coordinate transformation and geodesic screening distances."""

from __future__ import annotations

from collections.abc import Iterable

from pyproj import CRS, Geod, Transformer

from .models import Node


WGS84 = CRS.from_epsg(4326)


def coordinates_in_wgs84(
    nodes: Iterable[Node],
    nodes_crs: str,
) -> dict[str, tuple[float, float]]:
    """Transform node coordinates to WGS 84 longitude and latitude."""
    source_crs = CRS.from_user_input(nodes_crs)
    transformer = Transformer.from_crs(
        source_crs,
        WGS84,
        always_xy=True,
    )

    transformed: dict[str, tuple[float, float]] = {}

    for node in nodes:
        longitude, latitude = transformer.transform(
            node.longitude,
            node.latitude,
        )

        if not -180.0 <= longitude <= 180.0:
            raise ValueError(
                f"Transformed longitude is outside WGS 84 bounds for "
                f"node {node.node_id}: {longitude}"
            )

        if not -90.0 <= latitude <= 90.0:
            raise ValueError(
                f"Transformed latitude is outside WGS 84 bounds for "
                f"node {node.node_id}: {latitude}"
            )

        transformed[node.node_id] = (longitude, latitude)

    return transformed


def geodesic_distance_km(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    """Calculate ellipsoidal point-to-point distance in kilometres."""
    geod = Geod(ellps="WGS84")
    _, _, distance_m = geod.inv(
        first[0],
        first[1],
        second[0],
        second[1],
    )
    return float(distance_m) / 1000.0
