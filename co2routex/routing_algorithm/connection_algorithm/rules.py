"""Direction and node-role rules for candidate connections."""

from __future__ import annotations

from .models import Node, Settings


def is_supported_node_type(node_type: str, settings: Settings) -> bool:
    """Return whether a node type has a defined connection role."""
    normalized = node_type.strip().lower()
    return (
        normalized in settings.emitter_types
        or normalized in settings.storage_types
        or normalized == settings.transport_type
    )


def is_eligible_direction(
    origin: Node,
    destination: Node,
    settings: Settings,
) -> bool:
    """Return whether the directed pair is allowed by the role rules.

    Emitters may connect to storage/utilisation or transport nodes.
    Transport nodes may connect to storage/utilisation nodes and,
    optionally, to other transport nodes. Storage and utilisation nodes
    are terminal destinations and therefore never originate candidates.
    """
    if origin.node_id == destination.node_id:
        return False

    origin_type = origin.node_type
    destination_type = destination.node_type

    if origin_type in settings.emitter_types:
        return (
            destination_type in settings.storage_types
            or destination_type == settings.transport_type
        )

    if origin_type == settings.transport_type:
        if destination_type in settings.storage_types:
            return True

        return (
            settings.allow_transport_to_transport
            and destination_type == settings.transport_type
        )

    return False
