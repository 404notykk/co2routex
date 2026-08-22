import pandas as pd

from astar_router.inputs import _parse_matrix, _parse_nodes


def test_matrix_is_directional():
    nodes = _parse_nodes(pd.DataFrame({
        "node_id": [1, 2], "longitude": [5.0, 5.1], "latitude": [52.0, 52.1]
    }))
    matrix = pd.DataFrame([[0, 1], [0, 0]], index=[1, 2], columns=[1, 2])
    connections = _parse_matrix("pipeline", matrix, set(nodes))
    assert [(c.from_id, c.to_id) for c in connections] == [("1", "2")]

