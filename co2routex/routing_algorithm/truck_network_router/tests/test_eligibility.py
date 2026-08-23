from truck_router.eligibility import is_eligible_pair
from truck_router.models import Node


def make_node(node_id, node_type):
    return Node(
        node_id=node_id,
        node_name=str(node_id),
        longitude=0,
        latitude=0,
        altitude=10,
        annual_flux=None,
        node_type=node_type,
        country_code="NL",
    )


def test_baseline_allows_distinct_same_country_nodes():
    assert is_eligible_pair(make_node(1, "cement"), make_node(2, "storage"), "baseline")


def test_directed_chain_does_not_route_out_of_storage():
    assert not is_eligible_pair(
        make_node(1, "storage"), make_node(2, "cement"), "directed_chain"
    )


def test_directed_chain_allows_transport_to_storage():
    assert is_eligible_pair(
        make_node(1, "transport"), make_node(2, "storage"), "directed_chain"
    )
