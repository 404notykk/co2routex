"""Tests for candidate generation and distance screening."""

from connection_algorithm.generator import generate_candidates
from connection_algorithm.models import Node


def make_node(
    node_id: str,
    longitude: float,
    node_type: str,
    latitude: float = 52.0,
) -> Node:
    return Node(
        node_id=node_id,
        longitude=longitude,
        latitude=latitude,
        node_type=node_type,
    )


def pairs(candidates):
    return {(item.from_id, item.to_id) for item in candidates}


def test_two_nodes_create_only_emitter_to_storage(settings_factory):
    nodes = {
        "E": make_node("E", 5.0, "cement"),
        "S": make_node("S", 5.5, "storage"),
    }

    candidates = generate_candidates(nodes, settings_factory())
    assert pairs(candidates) == {("E", "S")}


def test_all_eligible_retains_all_valid_destinations(settings_factory):
    nodes = {
        "E": make_node("E", 5.0, "refinery"),
        "S1": make_node("S1", 5.1, "storage"),
        "S2": make_node("S2", 5.2, "utilisation"),
        "T": make_node("T", 5.3, "transport"),
    }

    candidates = generate_candidates(nodes, settings_factory())

    assert pairs(candidates) == {
        ("E", "S1"),
        ("E", "S2"),
        ("E", "T"),
        ("T", "S1"),
        ("T", "S2"),
    }


def test_emitter_to_emitter_is_disabled_in_baseline(settings_factory):
    nodes = {
        "A": make_node("A", 5.0, "cement"),
        "B": make_node("B", 5.5, "refinery"),
        "S": make_node("S", 6.0, "storage"),
    }

    candidates = generate_candidates(nodes, settings_factory())

    assert ("A", "B") not in pairs(candidates)
    assert ("B", "A") not in pairs(candidates)


def test_optional_emitter_rule_creates_one_direction(settings_factory):
    nodes = {
        "A": make_node("A", 5.0, "cement"),
        "B": make_node("B", 5.6, "refinery"),
        "S": make_node("S", 6.0, "storage"),
    }
    settings = settings_factory(
        emitter_to_emitter_rule="toward_nearest_destination",
        emitter_to_emitter_max_detour_factor=1.1,
    )

    candidates = generate_candidates(nodes, settings)
    candidate_pairs = pairs(candidates)

    assert ("A", "B") in candidate_pairs
    assert ("B", "A") not in candidate_pairs

    emitter_candidate = next(
        item
        for item in candidates
        if item.from_id == "A" and item.to_id == "B"
    )
    assert emitter_candidate.selection_rule == (
        "emitter_to_emitter_toward_nearest_destination"
    )


def test_optional_emitter_rule_rejects_excessive_detour(settings_factory):
    nodes = {
        "A": make_node("A", 5.0, "cement", latitude=52.0),
        "B": make_node("B", 5.7, "refinery", latitude=53.0),
        "S": make_node("S", 6.0, "storage", latitude=52.0),
    }
    settings = settings_factory(
        emitter_to_emitter_rule="toward_nearest_destination",
        emitter_to_emitter_max_detour_factor=1.05,
    )

    candidates = generate_candidates(nodes, settings)

    assert ("A", "B") not in pairs(candidates)
    assert ("B", "A") not in pairs(candidates)


def test_nearest_k_and_relative_threshold(settings_factory):
    nodes = {
        "E": make_node("E", 5.0, "waste_to_energy"),
        "S1": make_node("S1", 5.1, "storage"),
        "S2": make_node("S2", 5.14, "storage"),
        "S3": make_node("S3", 5.5, "storage"),
    }
    settings = settings_factory(
        method="nearest_k_with_relative_threshold",
        k=1,
        relative_distance_factor=1.5,
    )

    candidates = generate_candidates(nodes, settings)

    assert pairs(candidates) == {("E", "S1"), ("E", "S2")}


def test_unknown_node_type_is_rejected(settings_factory):
    nodes = {
        "E": make_node("E", 5.0, "steel"),
        "S": make_node("S", 5.5, "storage"),
    }

    try:
        generate_candidates(nodes, settings_factory())
    except ValueError as error:
        assert "unsupported node_type" in str(error)
    else:
        raise AssertionError("Expected unsupported node type to fail")
