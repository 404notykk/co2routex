import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import LineString, Point

from truck_router.network import load_road_network
from truck_router.routing import shortest_route


def write_network(path, oneway="yes"):
    roads = gpd.GeoDataFrame(
        {
            "highway": ["primary"],
            "oneway": [oneway],
            "geometry": [LineString([(0, 0), (1000, 0)])],
        },
        crs="EPSG:3857",
    )
    roads.to_file(path, layer="truck_network", driver="GPKG")


def test_edge_snapping_and_forward_oneway_route(tmp_path):
    path = tmp_path / "network.gpkg"
    write_network(path)
    network = load_road_network(path, "truck_network", "EPSG:3857", True)
    start = network.snap(Point(200, 20))
    end = network.snap(Point(800, 10))
    length, geometry = shortest_route(network, start, end)
    assert length == pytest.approx(600)
    assert geometry.length == pytest.approx(600)


def test_reverse_route_is_blocked_on_oneway_segment(tmp_path):
    path = tmp_path / "network.gpkg"
    write_network(path)
    network = load_road_network(path, "truck_network", "EPSG:3857", True)
    start = network.snap(Point(800, 0))
    end = network.snap(Point(200, 0))
    with pytest.raises(nx.NetworkXNoPath):
        shortest_route(network, start, end)
