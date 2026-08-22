import math

import numpy as np

from astar_router.astar import NoPathError, astar_search


class Transform:
    a = 100.0
    b = 0.0
    d = 0.0
    e = -100.0


def test_diagonal_route_uses_cell_geometry():
    costs = np.ones((3, 3))
    result = astar_search(costs, np.ones_like(costs, dtype=bool), Transform(), (0, 0), (2, 2))
    assert result.path == [(0, 0), (1, 1), (2, 2)]
    assert math.isclose(result.geometric_length, 200 * math.sqrt(2))
    assert math.isclose(result.weighted_cost, result.geometric_length)


def test_resistance_changes_selected_path():
    costs = np.ones((3, 5))
    costs[1, 1:4] = 100
    result = astar_search(costs, np.ones_like(costs, dtype=bool), Transform(), (1, 0), (1, 4))
    assert not any(row == 1 and 1 <= col <= 3 for row, col in result.path)


def test_no_corner_cutting():
    costs = np.ones((2, 2))
    mask = np.array([[True, False], [False, True]])
    try:
        astar_search(costs, mask, Transform(), (0, 0), (1, 1), prevent_corner_cutting=True)
    except NoPathError:
        pass
    else:
        raise AssertionError("Expected NoPathError")

