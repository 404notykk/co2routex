"""Generate directed candidate connections from nodes and rules."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import combinations

from .distance import coordinates_in_wgs84, geodesic_distance_km
from .models import CandidateConnection, Node, Settings
from .rules import is_eligible_direction, is_supported_node_type


def generate_candidates(
    nodes: dict[str, Node],
    settings: Settings,
) -> list[CandidateConnection]:
    """Generate candidates using the configured method.

    This stage does not use annual flow, storage capacity, route cost, or
    any other MILP decision variable. Distance is used only to screen or
    rank otherwise eligible directed pairs.
    """
    _validate_node_types(nodes.values(), settings)

    coordinates = coordinates_in_wgs84(
        nodes.values(),
        settings.nodes_crs,
    )
    all_candidates = _eligible_candidates(
        nodes,
        coordinates,
        settings,
    )

    if settings.emitter_to_emitter_rule == "toward_nearest_destination":
        all_candidates.extend(
            _emitter_to_emitter_candidates(
                nodes,
                coordinates,
                settings,
            )
        )

    if settings.method == "all_eligible":
        selected = all_candidates
    else:
        selected = _screen_by_origin(all_candidates, settings)

    return sorted(
        selected,
        key=lambda item: (
            item.from_id,
            item.geodesic_distance_km,
            item.to_id,
        ),
    )


def _validate_node_types(
    nodes: Iterable[Node],
    settings: Settings,
) -> None:
    unsupported = sorted(
        {
            node.node_type
            for node in nodes
            if not is_supported_node_type(node.node_type, settings)
        }
    )

    if unsupported:
        raise ValueError(
            "The nodes worksheet contains unsupported node_type values: "
            f"{unsupported}. Add them to emitter_types or storage_types, "
            "or correct the workbook."
        )


def _eligible_candidates(
    nodes: dict[str, Node],
    coordinates: dict[str, tuple[float, float]],
    settings: Settings,
) -> list[CandidateConnection]:
    candidates: list[CandidateConnection] = []

    for origin in nodes.values():
        for destination in nodes.values():
            if not is_eligible_direction(origin, destination, settings):
                continue

            distance_km = geodesic_distance_km(
                coordinates[origin.node_id],
                coordinates[destination.node_id],
            )

            if (
                settings.max_distance_km is not None
                and distance_km > settings.max_distance_km
            ):
                continue

            candidates.append(
                CandidateConnection(
                    from_id=origin.node_id,
                    to_id=destination.node_id,
                    from_type=origin.node_type,
                    to_type=destination.node_type,
                    geodesic_distance_km=distance_km,
                )
            )

    return candidates


def _emitter_to_emitter_candidates(
    nodes: dict[str, Node],
    coordinates: dict[str, tuple[float, float]],
    settings: Settings,
) -> list[CandidateConnection]:
    """Direct optional emitter pairs toward the nearest destination.

    For each unordered emitter pair, the emitter farther from its own
    nearest storage/utilisation destination may connect to the closer
    emitter. The candidate is retained only when travelling through the
    closer emitter stays within the configured detour factor. This rule
    creates at most one direction for each emitter pair.
    """
    emitters = [
        node
        for node in nodes.values()
        if node.node_type in settings.emitter_types
    ]
    destinations = [
        node
        for node in nodes.values()
        if node.node_type in settings.storage_types
    ]

    if len(emitters) < 2:
        return []

    if not destinations:
        raise ValueError(
            "The toward_nearest_destination emitter-to-emitter rule "
            "requires at least one storage or utilisation node"
        )

    nearest_destination_distance = {
        emitter.node_id: min(
            geodesic_distance_km(
                coordinates[emitter.node_id],
                coordinates[destination.node_id],
            )
            for destination in destinations
        )
        for emitter in emitters
    }
    candidates: list[CandidateConnection] = []

    for first, second in combinations(emitters, 2):
        first_distance = nearest_destination_distance[first.node_id]
        second_distance = nearest_destination_distance[second.node_id]

        if abs(first_distance - second_distance) <= 1e-9:
            continue

        if first_distance > second_distance:
            origin, destination = first, second
            origin_destination_distance = first_distance
            destination_destination_distance = second_distance
        else:
            origin, destination = second, first
            origin_destination_distance = second_distance
            destination_destination_distance = first_distance

        pair_distance = geodesic_distance_km(
            coordinates[origin.node_id],
            coordinates[destination.node_id],
        )

        if (
            settings.max_distance_km is not None
            and pair_distance > settings.max_distance_km
        ):
            continue

        route_via_destination = (
            pair_distance + destination_destination_distance
        )
        allowed_route_distance = (
            settings.emitter_to_emitter_max_detour_factor
            * origin_destination_distance
        )

        if route_via_destination > allowed_route_distance:
            continue

        candidates.append(
            CandidateConnection(
                from_id=origin.node_id,
                to_id=destination.node_id,
                from_type=origin.node_type,
                to_type=destination.node_type,
                geodesic_distance_km=pair_distance,
                selection_rule=(
                    "emitter_to_emitter_toward_nearest_destination"
                ),
            )
        )

    return candidates


def _screen_by_origin(
    candidates: list[CandidateConnection],
    settings: Settings,
) -> list[CandidateConnection]:
    """Apply nearest-k and relative-distance screening per origin."""
    by_origin: dict[str, list[CandidateConnection]] = {}

    for candidate in candidates:
        by_origin.setdefault(candidate.from_id, []).append(candidate)

    selected: list[CandidateConnection] = []

    for origin_candidates in by_origin.values():
        ranked = sorted(
            origin_candidates,
            key=lambda item: (
                item.geodesic_distance_km,
                item.to_id,
            ),
        )
        nearest_distance = ranked[0].geodesic_distance_km
        relative_limit = (
            nearest_distance * settings.relative_distance_factor
        )

        keep_ids = {
            candidate.to_id
            for candidate in ranked[: settings.k]
        }
        keep_ids.update(
            candidate.to_id
            for candidate in ranked
            if candidate.geodesic_distance_km <= relative_limit
        )

        selected.extend(
            candidate
            for candidate in ranked
            if candidate.to_id in keep_ids
        )

    return selected
