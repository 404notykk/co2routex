"""Load and interact with the spatial resistance raster."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.transform import rowcol, xy

from .models import Cell, Node


class ResistanceRaster:
    """
    A single-band spatial resistance raster used for A* routing.

    Finite raster cells are traversable unless zero-valued cells are
    configured as barriers. NoData, NaN and infinite cells are always
    treated as barriers.

    The raster must use a projected CRS whose horizontal units are metres.
    """

    def __init__(
        self,
        path: Path,
        *,
        zero_is_barrier: bool = False,
    ) -> None:
        self.path = Path(path)

        if not self.path.exists():
            raise FileNotFoundError(self.path)

        with rasterio.open(self.path) as source:
            if source.count != 1:
                raise ValueError(
                    "The resistance raster must contain exactly one band"
                )

            if source.crs is None:
                raise ValueError(
                    "The resistance raster does not define a CRS"
                )

            crs = CRS.from_user_input(source.crs)
            _validate_metric_projected_crs(crs)

            masked = source.read(1, masked=True)
            resistance = masked.astype(np.float64).filled(np.nan)

            self.resistance = np.asarray(
                resistance,
                dtype=np.float64,
            )
            self.transform = source.transform
            self.crs = crs
            self.width = source.width
            self.height = source.height
            self.nodata = source.nodata

        if self.resistance.ndim != 2:
            raise ValueError(
                "The resistance raster must be two-dimensional"
            )

        finite = np.isfinite(self.resistance)

        if np.any(self.resistance[finite] < 0):
            minimum = float(np.min(self.resistance[finite]))
            raise ValueError(
                "Negative spatial resistance values are not supported; "
                f"minimum value found: {minimum}"
            )

        self.traversable = finite.copy()

        if zero_is_barrier:
            self.traversable &= self.resistance > 0

        if not self.traversable.any():
            raise ValueError(
                "The resistance raster contains no traversable cells"
            )

    def node_cells(
        self,
        nodes: dict[str, Node],
        nodes_crs: str,
        snap_radius_cells: int,
    ) -> dict[str, dict[str, Any]]:
        """
        Transform nodes into the raster CRS and assign raster cells.

        A node falling on a non-traversable cell is moved to the nearest
        traversable cell within ``snap_radius_cells``.
        """
        if snap_radius_cells < 0:
            raise ValueError(
                "snap_radius_cells cannot be negative"
            )

        source_crs = CRS.from_user_input(nodes_crs)

        transformer = Transformer.from_crs(
            source_crs,
            self.crs,
            always_xy=True,
        )

        locations: dict[str, dict[str, Any]] = {}

        for node_id, node in nodes.items():
            x, y = transformer.transform(
                node.longitude,
                node.latitude,
            )

            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError(
                    f"Could not transform coordinates for node {node_id}"
                )

            row, col = rowcol(self.transform, x, y)
            original_cell = (int(row), int(col))

            if not self.contains(original_cell):
                raise ValueError(
                    f"Node {node_id} falls outside the resistance raster"
                )

            snapped_cell = self.snap(
                original_cell,
                snap_radius_cells,
                target_xy=(x, y),
            )

            snapped_x, snapped_y = self.cell_xy(snapped_cell)

            locations[node_id] = {
                "cell": snapped_cell,
                "original_cell": original_cell,
                "projected_x": float(x),
                "projected_y": float(y),
                "snapped_x": snapped_x,
                "snapped_y": snapped_y,
                "snap_distance_m": math.hypot(
                    snapped_x - x,
                    snapped_y - y,
                ),
            }

        return locations

    def contains(self, cell: Cell) -> bool:
        """Return whether a cell lies within the raster bounds."""
        row, col = cell

        return (
            0 <= row < self.height
            and 0 <= col < self.width
        )

    def snap(
        self,
        cell: Cell,
        radius: int,
        *,
        target_xy: tuple[float, float] | None = None,
    ) -> Cell:
        """
        Return the nearest traversable cell within a search radius.

        The radius is expressed in raster cells. Candidate cells within that
        radius are ranked using their map-coordinate distance to the original
        node position.
        """
        if radius < 0:
            raise ValueError("Snap radius cannot be negative")

        if not self.contains(cell):
            raise ValueError(
                f"Cannot snap cell {cell}: it is outside the raster"
            )

        row, col = cell

        if self.traversable[row, col]:
            return cell

        if target_xy is None:
            target_xy = self.cell_xy(cell)

        target_x, target_y = target_xy

        best_key: tuple[float, int, int, int] | None = None
        best_cell: Cell | None = None

        for row_offset in range(-radius, radius + 1):
            for column_offset in range(-radius, radius + 1):
                grid_distance_squared = (
                    row_offset * row_offset
                    + column_offset * column_offset
                )

                if grid_distance_squared > radius * radius:
                    continue

                candidate = (
                    row + row_offset,
                    col + column_offset,
                )

                if not self.contains(candidate):
                    continue

                candidate_row, candidate_col = candidate

                if not self.traversable[
                    candidate_row,
                    candidate_col,
                ]:
                    continue

                candidate_x, candidate_y = self.cell_xy(candidate)

                map_distance_squared = (
                    (candidate_x - target_x) ** 2
                    + (candidate_y - target_y) ** 2
                )

                # Remaining values provide deterministic tie-breaking.
                candidate_key = (
                    map_distance_squared,
                    grid_distance_squared,
                    candidate_row,
                    candidate_col,
                )

                if best_key is None or candidate_key < best_key:
                    best_key = candidate_key
                    best_cell = candidate

        if best_cell is None:
            raise ValueError(
                f"No traversable cell was found within {radius} cells "
                f"of raster cell {cell}"
            )

        return best_cell

    def cell_xy(self, cell: Cell) -> tuple[float, float]:
        """Return the map coordinates of a raster-cell centre."""
        if not self.contains(cell):
            raise ValueError(
                f"Cell {cell} lies outside the resistance raster"
            )

        x_coordinate, y_coordinate = xy(
            self.transform,
            cell[0],
            cell[1],
            offset="center",
        )

        return float(x_coordinate), float(y_coordinate)


def _validate_metric_projected_crs(crs: CRS) -> None:
    """Require a projected CRS whose horizontal units are metres."""
    if not crs.is_projected:
        raise ValueError(
            "The resistance raster CRS must be projected; "
            "a geographic longitude/latitude CRS is not supported"
        )

    horizontal_axes = list(crs.axis_info[:2])

    if len(horizontal_axes) < 2:
        raise ValueError(
            "Could not determine the horizontal units of the raster CRS"
        )

    unit_names: list[str] = []

    for axis in horizontal_axes:
        unit_name = axis.unit_name or "unknown"
        unit_names.append(unit_name)

        conversion_factor = axis.unit_conversion_factor

        if conversion_factor is None or not math.isclose(
            float(conversion_factor),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "The resistance raster CRS must use metres as its "
                f"horizontal units; found {unit_names}"
            )