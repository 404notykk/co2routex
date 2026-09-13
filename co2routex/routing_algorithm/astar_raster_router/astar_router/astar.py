"""A* search on a raster-based spatial resistance surface."""

from __future__ import annotations

import heapq
import math
import time
from itertools import count

import numpy as np

from .models import Cell, SearchResult


class NoPathError(RuntimeError):
    """Raised when no traversable route exists between two raster cells."""


class PreparedGrid:
    """Validated, read-only arrays shared by searches. Do not mutate backing data.

    Validation uses row chunks rather than copying every valid pixel at once.
    Construct once per input raster, or let astar_search construct it for a
    standalone call. Pipeline searches use search_prepared directly.
    """
    def __init__(self, resistance, traversable, transform):
        self.resistance = np.asarray(resistance)
        self.traversable = np.asarray(traversable, dtype=bool)
        if self.resistance.ndim != 2 or self.traversable.shape != self.resistance.shape:
            raise ValueError("Resistance and mask must be matching 2-D arrays")
        if self.resistance.dtype.kind not in "fiu":
            raise ValueError("Resistance must be real numeric values")
        if not all(math.isfinite(v) for v in (transform.a, transform.b, transform.d, transform.e)):
            raise ValueError("Invalid affine transform")
        if transform.a * transform.e - transform.b * transform.d == 0:
            raise ValueError("Singular affine transform")
        self.minimum = math.inf
        self.maximum = -math.inf
        self.valid_cells = 0
        for row in range(0, self.resistance.shape[0], 128):
            values = self.resistance[row:row+128][self.traversable[row:row+128]]
            if not values.size:
                continue
            if not np.isfinite(values).all() or np.any(values < 0):
                raise ValueError("Traversable resistance must be finite and non-negative")
            self.minimum = min(self.minimum, float(values.min()))
            self.maximum = max(self.maximum, float(values.max()))
            self.valid_cells += values.size
        if not self.valid_cells:
            raise ValueError("Raster has no traversable cells")
        self.transform = transform
        self.resistance.flags.writeable = False
        self.traversable.flags.writeable = False


def astar_search(resistance, traversable, transform, start, goal, *,
                 connectivity=8, prevent_corner_cutting=True, metrics=None):
    """Safe standalone entry: validates new arrays, then runs exact A*."""
    return search_prepared(PreparedGrid(resistance, traversable, transform),
                           start, goal, connectivity=connectivity,
                           prevent_corner_cutting=prevent_corner_cutting,
                           metrics=metrics)


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


def search_prepared(
    grid: PreparedGrid,
    start: Cell,
    goal: Cell,
    *,
    connectivity: int = 8,
    prevent_corner_cutting: bool = True,
    metrics: dict | None = None,
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
    prepare_started = time.perf_counter()
    metrics = metrics if metrics is not None else {}
    resistance, traversable, transform = grid.resistance, grid.traversable, grid.transform
    directions = _neighbours(connectivity)
    for label, cell in (("start", start), ("goal", goal)):
        row, col = cell
        if not (0 <= row < resistance.shape[0] and 0 <= col < resistance.shape[1]):
            raise ValueError(f"{label} cell is outside the raster")
        if not traversable[row, col]:
            raise ValueError(f"{label} cell is not traversable")

    if start == goal:
        metrics.update(search_setup_wall_s=time.perf_counter()-prepare_started,
                       search_wall_s=0.0, path_build_wall_s=0.0,
                       explored_cells=1, discovered_cells=1, queue_peak_entries=1)
        return SearchResult(
            [start],
            0.0,
            0.0,
            1,
        )

    minimum_resistance = grid.minimum
    step_lengths = {direction: _distance(transform, (0, 0), direction)
                    for direction in directions}

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
    peak_queue = 1
    metrics["search_setup_wall_s"] = time.perf_counter() - prepare_started
    search_started = time.perf_counter()

    while queue:
        _, _, current = heapq.heappop(queue)

        if current in closed:
            continue

        closed.add(current)

        if current == goal:
            metrics.update(search_wall_s=time.perf_counter()-search_started,
                           explored_cells=len(closed), discovered_cells=len(best_accumulated_resistance),
                           queue_peak_entries=peak_queue)
            build_started = time.perf_counter()
            path = _reconstruct(parent, goal)

            length_map_units = sum(
                _distance(transform, first, second)
                for first, second in zip(path, path[1:])
            )

            result = SearchResult(
                path,
                best_accumulated_resistance[goal],
                length_map_units,
                len(closed),
            )
            metrics["path_build_wall_s"] = time.perf_counter() - build_started
            return result

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

            step_length = step_lengths[(row_offset, column_offset)]

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
            peak_queue = max(peak_queue, len(queue))

    metrics.update(search_wall_s=time.perf_counter()-search_started,
                   path_build_wall_s=0.0, explored_cells=len(closed),
                   discovered_cells=len(best_accumulated_resistance), queue_peak_entries=peak_queue)
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
