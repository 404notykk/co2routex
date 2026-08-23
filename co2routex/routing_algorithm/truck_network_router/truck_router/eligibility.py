from __future__ import annotations

from .input import id_key
from .models import Node


def node_category(node: Node) -> str:
    value = node.node_type.strip().lower()
    if value == "storage":
        return "storage"
    if value == "transport":
        return "transport"
    return "emitter"


def is_eligible_pair(
    origin: Node,
    destination: Node,
    policy: str,
    existing_nonzero_pairs: set[tuple[str, str]] | None = None,
) -> bool:
    if id_key(origin.node_id) == id_key(destination.node_id):
        return False
    if origin.country_code != destination.country_code:
        return False

    if existing_nonzero_pairs is not None:
        return (
            id_key(origin.node_id),
            id_key(destination.node_id),
        ) in existing_nonzero_pairs

    if policy == "baseline":
        return True

    if policy == "directed_chain":
        origin_category = node_category(origin)
        destination_category = node_category(destination)
        if origin_category == "storage":
            return False
        if origin_category == "transport":
            return destination_category in {"transport", "storage"}
        return destination_category in {"emitter", "transport", "storage"}

    raise ValueError(f"Unsupported connection policy: {policy}")
