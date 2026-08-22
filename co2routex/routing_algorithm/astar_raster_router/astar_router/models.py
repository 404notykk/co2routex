"""Shared data models for the A* raster router."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Raster cells are represented as (row, column).
Cell = tuple[int, int]


@dataclass(frozen=True, slots=True)
class Node:
    """A geographic node used in the CO2 transport network."""

    node_id: str
    longitude: float
    latitude: float
    node_name: str = ""
    altitude: float = 10.0
    annual_flux: float | None = None
    node_type: str | None = None
    country_code: str | None = None
    additional_attributes: dict[str, Any] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        """Validate the node data."""
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise ValueError("node_id must be a non-empty string")

        if not isinstance(self.node_name, str):
            raise TypeError(
                f"node_name must be a string for node {self.node_id}"
            )

        if not math.isfinite(self.longitude):
            raise ValueError(
                f"longitude must be finite for node {self.node_id}"
            )

        if not math.isfinite(self.latitude):
            raise ValueError(
                f"latitude must be finite for node {self.node_id}"
            )

        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError(
                f"longitude must be between -180 and 180 for "
                f"node {self.node_id}: {self.longitude}"
            )

        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError(
                f"latitude must be between -90 and 90 for "
                f"node {self.node_id}: {self.latitude}"
            )

        if not math.isfinite(self.altitude):
            raise ValueError(
                f"altitude must be finite for node {self.node_id}"
            )

        if self.annual_flux is not None:
            if not math.isfinite(self.annual_flux):
                raise ValueError(
                    f"annual_flux must be finite for "
                    f"node {self.node_id}"
                )

            if self.annual_flux < 0:
                raise ValueError(
                    f"annual_flux cannot be negative for "
                    f"node {self.node_id}: {self.annual_flux}"
                )

        if (
            self.node_type is not None
            and (
                not isinstance(self.node_type, str)
                or not self.node_type.strip()
            )
        ):
            raise ValueError(
                f"node_type cannot be an empty string for "
                f"node {self.node_id}"
            )

        if (
            self.country_code is not None
            and (
                not isinstance(self.country_code, str)
                or not self.country_code.strip()
            )
        ):
            raise ValueError(
                f"country_code cannot be an empty string for "
                f"node {self.node_id}"
            )

        if not isinstance(self.additional_attributes, dict):
            raise TypeError(
                f"additional_attributes must be a dictionary for "
                f"node {self.node_id}"
            )


@dataclass(frozen=True, slots=True)
class Connection:
    """A directed connection that must be evaluated by the router."""

    mode: str
    from_id: str
    to_id: str

    def __post_init__(self) -> None:
        """Validate the connection identifiers."""
        if not self.mode.strip():
            raise ValueError("Connection mode cannot be blank")

        if not self.from_id.strip():
            raise ValueError(
                "Connection origin node ID cannot be blank"
            )

        if not self.to_id.strip():
            raise ValueError(
                "Connection destination node ID cannot be blank"
            )

        if self.from_id == self.to_id:
            raise ValueError(
                "A connection cannot link a node to itself: "
                f"{self.from_id}"
            )


@dataclass(slots=True)
class SearchResult:
    """Result returned by one A* raster search."""

    path: list[Cell]
    accumulated_resistance: float
    geometric_length_map_units: float
    explored_cells: int

    def __post_init__(self) -> None:
        """Validate the search result."""
        if not self.path:
            raise ValueError(
                "A successful search result must contain a path"
            )

        if not math.isfinite(self.accumulated_resistance):
            raise ValueError(
                "accumulated_resistance must be finite"
            )

        if self.accumulated_resistance < 0:
            raise ValueError(
                "accumulated_resistance cannot be negative"
            )

        if not math.isfinite(self.geometric_length_map_units):
            raise ValueError(
                "geometric_length_map_units must be finite"
            )

        if self.geometric_length_map_units < 0:
            raise ValueError(
                "geometric_length_map_units cannot be negative"
            )

        if self.explored_cells < 1:
            raise ValueError(
                "explored_cells must be at least 1"
            )


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated configuration for one A* routing run."""

    raster_path: Path
    workbook_path: Path
    output_dir: Path

    nodes_sheet: str = "nodes"

    # Retained for compatibility with the original
    # pipeline-only configuration.
    pipeline_sheet: str = "pipeline"

    # Transport-mode sheets processed by the current router.
    mode_sheets: tuple[str, ...] = ("pipeline",)

    nodes_crs: str = "EPSG:4326"

    connectivity: int = 8
    prevent_corner_cutting: bool = True
    snap_radius_cells: int = 10
    zero_is_barrier: bool = False
    cache_reverse_routes: bool = True

    output_workbook_name: str = "node_metrics_routed.xlsx"
    routes_filename: str = "routes.gpkg"

    def __post_init__(self) -> None:
        """Validate configuration values independent of file existence."""
        if not isinstance(self.raster_path, Path):
            raise TypeError(
                "raster_path must be a pathlib.Path"
            )

        if not isinstance(self.workbook_path, Path):
            raise TypeError(
                "workbook_path must be a pathlib.Path"
            )

        if not isinstance(self.output_dir, Path):
            raise TypeError(
                "output_dir must be a pathlib.Path"
            )

        if (
            not isinstance(self.nodes_sheet, str)
            or not self.nodes_sheet.strip()
        ):
            raise ValueError(
                "nodes_sheet cannot be blank"
            )

        if (
            not isinstance(self.pipeline_sheet, str)
            or not self.pipeline_sheet.strip()
        ):
            raise ValueError(
                "pipeline_sheet cannot be blank"
            )

        if self.nodes_sheet == self.pipeline_sheet:
            raise ValueError(
                "nodes_sheet and pipeline_sheet must be different"
            )

        if isinstance(self.mode_sheets, str):
            raise TypeError(
                "mode_sheets must be a sequence of sheet names, "
                "not a single string"
            )

        try:
            normalized_mode_sheets = tuple(self.mode_sheets)
        except TypeError as error:
            raise TypeError(
                "mode_sheets must be an iterable of sheet names"
            ) from error

        if not normalized_mode_sheets:
            raise ValueError(
                "mode_sheets must contain at least one sheet"
            )

        if any(
            not isinstance(sheet, str) or not sheet.strip()
            for sheet in normalized_mode_sheets
        ):
            raise ValueError(
                "Every mode_sheets entry must be a non-empty string"
            )

        normalized_mode_sheets = tuple(
            sheet.strip()
            for sheet in normalized_mode_sheets
        )

        if (
            len(set(normalized_mode_sheets))
            != len(normalized_mode_sheets)
        ):
            raise ValueError(
                "mode_sheets cannot contain duplicate sheet names"
            )

        if self.nodes_sheet in normalized_mode_sheets:
            raise ValueError(
                "nodes_sheet cannot also be included in mode_sheets"
            )

        # Settings is frozen, so object.__setattr__ is required
        # to store the validated tuple.
        object.__setattr__(
            self,
            "mode_sheets",
            normalized_mode_sheets,
        )

        if (
            not isinstance(self.nodes_crs, str)
            or not self.nodes_crs.strip()
        ):
            raise ValueError(
                "nodes_crs cannot be blank"
            )

        if self.connectivity not in {4, 8}:
            raise ValueError(
                "connectivity must be either 4 or 8"
            )

        if not isinstance(self.snap_radius_cells, int):
            raise TypeError(
                "snap_radius_cells must be an integer"
            )

        if self.snap_radius_cells < 0:
            raise ValueError(
                "snap_radius_cells cannot be negative"
            )

        if not isinstance(self.prevent_corner_cutting, bool):
            raise TypeError(
                "prevent_corner_cutting must be a boolean"
            )

        if not isinstance(self.zero_is_barrier, bool):
            raise TypeError(
                "zero_is_barrier must be a boolean"
            )

        if not isinstance(self.cache_reverse_routes, bool):
            raise TypeError(
                "cache_reverse_routes must be a boolean"
            )

        if (
            not isinstance(self.output_workbook_name, str)
            or not self.output_workbook_name.strip()
        ):
            raise ValueError(
                "output_workbook_name cannot be blank"
            )

        if (
            Path(self.output_workbook_name).suffix.lower()
            != ".xlsx"
        ):
            raise ValueError(
                "output_workbook_name must use the .xlsx extension"
            )

        if (
            not isinstance(self.routes_filename, str)
            or not self.routes_filename.strip()
        ):
            raise ValueError(
                "routes_filename cannot be blank"
            )

        if Path(self.routes_filename).suffix.lower() != ".gpkg":
            raise ValueError(
                "routes_filename must use the .gpkg extension"
            )
