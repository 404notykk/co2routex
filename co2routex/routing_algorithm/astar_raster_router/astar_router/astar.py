"""A* search on a raster-based spatial resistance surface."""

from __future__ import annotations

import heapq
import math
from itertools import count

import numpy as np

from .models import Cell, SearchResult


class NoPathError(RuntimeError):
    """Raised when no traversable route exists between two raster cells."""


def _distance(transform, a: Cell, b: Cell) -> float:
    """Return the map-unit distance between two raster-cell centres.

    The calculation uses the complete affine transformation and therefore
    supports rotated rasters and non-square cells.
    """
    row_difference = b[0] - a[0]
    column_difference = b[1] - a[1]

    x_difference = (
        transform.a * column_difference
        + transform.b * row_difference
    )
    y_difference = (
        transform.d * column_difference
        + transform.e * row_difference
    )

    return math.hypot(x_difference, y_difference)


def _heuristic(
    transform,
    cell: Cell,
    goal: Cell,
    minimum_resistance: float,
) -> float:
    """Return an admissible estimate of the remaining spatial resistance."""
    return _distance(transform, cell, goal) * minimum_resistance


def _neighbours(connectivity: int) -> tuple[tuple[int, int], ...]:
    """Return the permitted neighbour offsets for the selected connectivity."""
    if connectivity == 4:
        return (
            (-1, 0),
            (0, -1),
            (0, 1),
            (1, 0),
        )

    if connectivity == 8:
        return (
            (-1, -1),
            (-1, 0),
            (-1, 1),
            (0, -1),
            (0, 1),
            (1, -1),
            (1, 0),
            (1, 1),
        )

    raise ValueError("connectivity must be 4 or 8")


def _validate_inputs(
    resistance: np.ndarray,
    traversable: np.ndarray,
    start: Cell,
    goal: Cell,
) -> None:
    """Validate the raster arrays and start and goal cells."""
    if resistance.ndim != 2:
        raise ValueError("resistance must be a 2-D array")

    if traversable.ndim != 2:
        raise ValueError("traversable must be a 2-D array")

    if resistance.shape != traversable.shape:
        raise ValueError(
            "resistance and traversable must have the same shape"
        )

    if not np.issubdtype(resistance.dtype, np.number):
        raise ValueError("resistance must contain numeric values")

    rows, columns = resistance.shape

    for label, cell in (("start", start), ("goal", goal)):
        row, column = cell

        if not (0 <= row < rows and 0 <= column < columns):
            raise ValueError(f"{label} cell is outside the raster")

        if not traversable[row, column]:
            raise ValueError(f"{label} cell is not traversable")

    valid_resistance = resistance[traversable]

    if valid_resistance.size == 0:
        raise ValueError("the raster contains no traversable cells")

    if not np.all(np.isfinite(valid_resistance)):
        raise ValueError(
            "traversable cells must contain finite spatial resistance values"
        )

    if np.any(valid_resistance < 0):
        raise ValueError(
            "spatial resistance values must be non-negative"
        )


def astar_search(
    resistance: np.ndarray,
    traversable: np.ndarray,
    transform,
    start: Cell,
    goal: Cell,
    *,
    connectivity: int = 8,
    prevent_corner_cutting: bool = True,
) -> SearchResult:
    """Find a spatially least-resistant path across a raster.

    The resistance array contains relative spatial cost-resistance factors,
    not monetary construction costs.

    For adjacent cells i and j, movement resistance is calculated as:

        movement distance × mean resistance of cells i and j

    The returned route length is expressed in the linear unit of the raster
    coordinate reference system. Conversion to kilometres is performed later
    in the routing workflow.

    Parameters
    ----------
    resistance:
        Two-dimensional array of spatial cost-resistance factors.
    traversable:
        Boolean array indicating which raster cells may be crossed.
    transform:
        Affine transformation associated with the resistance raster.
    start:
        Starting raster cell as ``(row, column)``.
    goal:
        Destination raster cell as ``(row, column)``.
    connectivity:
        Neighbourhood connectivity. Must be either 4 or 8.
    prevent_corner_cutting:
        When true, diagonal movement is prohibited if either adjacent
        orthogonal cell is not traversable.

    Returns
    -------
    SearchResult
        The cell path, accumulated spatial resistance, route length in raster
        map units, and number of explored cells.

    Raises
    ------
    ValueError
        If the inputs, cells, resistance values, or connectivity are invalid.
    NoPathError
        If no traversable route exists between the start and goal cells.
    """
    resistance = np.asarray(resistance)
    traversable = np.asarray(traversable, dtype=bool)

    directions = _neighbours(connectivity)
    _validate_inputs(resistance, traversable, start, goal)

    if start == goal:
        return SearchResult(
            [start],
            0.0,
            0.0,
            1,
        )

    minimum_resistance = float(np.min(resistance[traversable]))

    serial = count()

    queue: list[tuple[float, int, Cell]] = [
        (
            _heuristic(
                transform,
                start,
                goal,
                minimum_resistance,
            ),
            next(serial),
            start,
        )
    ]

    best_accumulated_resistance: dict[Cell, float] = {
        start: 0.0
    }
    parent: dict[Cell, Cell] = {}
    closed: set[Cell] = set()

    while queue:
        _, _, current = heapq.heappop(queue)

        if current in closed:
            continue

        closed.add(current)

        if current == goal:
            path = _reconstruct(parent, goal)

            length_map_units = sum(
                _distance(transform, first, second)
                for first, second in zip(path, path[1:])
            )

            return SearchResult(
                path,
                best_accumulated_resistance[goal],
                length_map_units,
                len(closed),
            )

        row, column = current

        for row_offset, column_offset in directions:
            neighbour_row = row + row_offset
            neighbour_column = column + column_offset
            neighbour = (neighbour_row, neighbour_column)

            if not (
                0 <= neighbour_row < resistance.shape[0]
                and 0 <= neighbour_column < resistance.shape[1]
            ):
                continue

            if not traversable[neighbour_row, neighbour_column]:
                continue

            if neighbour in closed:
                continue

            is_diagonal = (
                row_offset != 0
                and column_offset != 0
            )

            if (
                prevent_corner_cutting
                and is_diagonal
                and (
                    not traversable[row + row_offset, column]
                    or not traversable[row, column + column_offset]
                )
            ):
                continue

            step_length = _distance(
                transform,
                current,
                neighbour,
            )

            edge_resistance = (
                float(resistance[row, column])
                + float(
                    resistance[
                        neighbour_row,
                        neighbour_column,
                    ]
                )
            ) / 2.0

            candidate_resistance = (
                best_accumulated_resistance[current]
                + step_length * edge_resistance
            )

            previous_best = best_accumulated_resistance.get(
                neighbour,
                math.inf,
            )

            if candidate_resistance >= previous_best:
                continue

            best_accumulated_resistance[neighbour] = (
                candidate_resistance
            )
            parent[neighbour] = current

            estimated_total_resistance = (
                candidate_resistance
                + _heuristic(
                    transform,
                    neighbour,
                    goal,
                    minimum_resistance,
                )
            )

            heapq.heappush(
                queue,
                (
                    estimated_total_resistance,
                    next(serial),
                    neighbour,
                ),
            )

    raise NoPathError(
        f"No traversable path from {start} to {goal}"
    )


def _reconstruct(
    parent: dict[Cell, Cell],
    goal: Cell,
) -> list[Cell]:
    """Reconstruct a path by following parent cells back from the goal."""
    path = [goal]

    while path[-1] in parent:
        path.append(parent[path[-1]])

    path.reverse()
    return path