"""Load and interact with the spatial resistance raster."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.transform import rowcol, xy

from .models import Cell, Node
from .astar import PreparedGrid


class ResistanceRaster:
    """
    A single-band spatial resistance raster used for A* routing.

    Finite unmasked raster cells are traversable unless zero-valued cells are
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

        started = time.perf_counter()
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

            if source.dtypes[0] != "float32":
                raise ValueError("Expected a Float32 resistance file; no silent precision conversion is applied")
            self.resistance = source.read(1)
            raw_mask = source.read_masks(1)
            self.transform = source.transform
            self.crs = crs
            self.width = source.width
            self.height = source.height
            self.nodata = source.nodata
            self.metadata = dict(raster_path=str(self.path), width_cells=self.width,
                                 height_cells=self.height, disk_dtype=source.dtypes[0],
                                 memory_dtype=str(self.resistance.dtype), crs=source.crs.to_wkt(),
                                 compression=source.compression.value if source.compression else "NONE",
                                 block_rows=source.block_shapes[0][0], block_columns=source.block_shapes[0][1],
                                 cell_x_m=source.res[0], cell_y_m=source.res[1], nodata=source.nodata,
                                 file_size_mib=self.path.stat().st_size / 2**20)
        self.read_wall_s = time.perf_counter() - started
        started = time.perf_counter()

        if self.resistance.ndim != 2:
            raise ValueError(
                "The resistance raster must be two-dimensional"
            )

        self.traversable = raw_mask != 0
        del raw_mask
        for row in range(0, self.height, 128):
            values = self.resistance[row:row+128]
            valid = self.traversable[row:row+128]
            valid &= np.isfinite(values)
            if np.any(values[valid] < 0):
                raise ValueError("Negative resistance values are not supported")
            if zero_is_barrier:
                valid &= values > 0
        self.grid = PreparedGrid(self.resistance, self.traversable, self.transform)
        self.prepare_wall_s = time.perf_counter() - started
        self.metadata.update(minimum_resistance=self.grid.minimum, maximum_resistance=self.grid.maximum,
                             valid_cells=int(self.grid.valid_cells), resistance_array_mib=self.resistance.nbytes / 2**20,
                             mask_array_mib=self.traversable.nbytes / 2**20, search_domain="full_valid_raster")

    def node_cells(
        self,
        nodes: dict[str, Node],
        nodes_crs: str,
        snap_radius_cells: int,
    ) -> dict[str, dict[str, Any]]:
        """
        Transform nodes and require valid containing cells, without snapping.

        snap_radius_cells is retained as a compatibility setting, but must be
        zero. The returned legacy snap_distance_m measures only the offset to
        the containing cell center; reports use cell_center_offset_m.
        """
        if snap_radius_cells != 0:
            raise ValueError("Strict node validation requires snap_radius_cells: 0")

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

            if not self.traversable[original_cell]:
                raise ValueError(f"Node {node_id} ({node.node_name}) falls on NoData/prohibited cell "
                                 f"{original_cell}; no snapping is allowed. Check coordinates and raster coverage.")
            snapped_cell = original_cell

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
