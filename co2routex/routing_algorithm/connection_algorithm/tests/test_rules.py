"""Tests for node-type direction rules."""

from connection_algorithm.models import Node
from connection_algorithm.rules import is_eligible_direction


def make_node(node_id: str, node_type: str) -> Node:
    return Node(
        node_id=node_id,
        longitude=5.0,
        latitude=52.0,
        node_type=node_type,
    )


def test_emitter_to_storage_is_allowed(settings_factory):
    settings = settings_factory()
    assert is_eligible_direction(
        make_node("E", "cement"),
        make_node("S", "storage"),
        settings,
    )


def test_storage_to_emitter_is_not_allowed(settings_factory):
    settings = settings_factory()
    assert not is_eligible_direction(
        make_node("S", "storage"),
        make_node("E", "cement"),
        settings,
    )


def test_transport_to_transport_is_configurable(settings_factory):
    first = make_node("T1", "transport")
    second = make_node("T2", "transport")

    assert is_eligible_direction(
        first,
        second,
        settings_factory(allow_transport_to_transport=True),
    )
    assert not is_eligible_direction(
        first,
        second,
        settings_factory(allow_transport_to_transport=False),
    )
