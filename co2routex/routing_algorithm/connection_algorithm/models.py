"""Data models for candidate-connection generation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SUPPORTED_METHODS = {
    "all_eligible",
    "nearest_k_with_relative_threshold",
}

SUPPORTED_EMITTER_TO_EMITTER_RULES = {
    "disabled",
    "toward_nearest_destination",
}


@dataclass(frozen=True, slots=True)
class Node:
    """A node read from the network workbook."""

    node_id: str
    longitude: float
    latitude: float
    node_type: str
    node_name: str = ""
    additional_attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise ValueError("node_id must be a non-empty string")

        if not isinstance(self.node_name, str):
            raise TypeError(
                f"node_name must be a string for node {self.node_id}"
            )

        if not isinstance(self.node_type, str) or not self.node_type.strip():
            raise ValueError(
                f"node_type must be a non-empty string for node "
                f"{self.node_id}"
            )

        if not math.isfinite(self.longitude):
            raise ValueError(
                f"longitude must be finite for node {self.node_id}"
            )

        if not math.isfinite(self.latitude):
            raise ValueError(
                f"latitude must be finite for node {self.node_id}"
            )

        if not isinstance(self.additional_attributes, dict):
            raise TypeError(
                f"additional_attributes must be a dictionary for node "
                f"{self.node_id}"
            )

        object.__setattr__(self, "node_id", self.node_id.strip())
        object.__setattr__(self, "node_name", self.node_name.strip())
        object.__setattr__(self, "node_type", self.node_type.strip().lower())


@dataclass(frozen=True, slots=True)
class CandidateConnection:
    """One directed candidate connection and its screening distance."""

    from_id: str
    to_id: str
    from_type: str
    to_type: str
    geodesic_distance_km: float
    selection_rule: str = "node_type"

    def __post_init__(self) -> None:
        if not self.from_id or not self.to_id:
            raise ValueError("Candidate node IDs cannot be blank")

        if self.from_id == self.to_id:
            raise ValueError("A candidate cannot connect a node to itself")

        if (
            not math.isfinite(self.geodesic_distance_km)
            or self.geodesic_distance_km < 0
        ):
            raise ValueError(
                "geodesic_distance_km must be finite and non-negative"
            )

        if (
            not isinstance(self.selection_rule, str)
            or not self.selection_rule.strip()
        ):
            raise ValueError("selection_rule must be a non-empty string")


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated settings for one connection-generation run."""

    workbook_path: Path

    nodes_sheet: str = "nodes"
    mode_sheets: tuple[str, ...] = ("pipeline",)

    nodes_crs: str = "EPSG:4326"
    method: str = "all_eligible"

    emitter_types: tuple[str, ...] = (
        "cement",
        "refinery",
        "waste_to_energy",
    )
    storage_types: tuple[str, ...] = (
        "storage",
        "utilisation",
    )
    transport_type: str = "transport"
    allow_transport_to_transport: bool = True

    emitter_to_emitter_rule: str = "disabled"
    emitter_to_emitter_max_detour_factor: float = 1.5

    k: int = 3
    relative_distance_factor: float = 1.5
    max_distance_km: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.workbook_path, Path):
            raise TypeError("workbook_path must be a pathlib.Path")

        if self.workbook_path.suffix.lower() != ".xlsx":
            raise ValueError("workbook_path must use the .xlsx extension")

        nodes_sheet = _clean_name(self.nodes_sheet, "nodes_sheet")
        mode_sheets = _clean_names(self.mode_sheets, "mode_sheets")

        if nodes_sheet in mode_sheets:
            raise ValueError(
                "nodes_sheet cannot also be included in mode_sheets"
            )

        if len(set(mode_sheets)) != len(mode_sheets):
            raise ValueError("mode_sheets cannot contain duplicates")

        nodes_crs = _clean_name(self.nodes_crs, "nodes_crs")
        method = _clean_name(self.method, "method").lower()

        if method not in SUPPORTED_METHODS:
            raise ValueError(
                f"method must be one of {sorted(SUPPORTED_METHODS)}"
            )

        emitter_types = _clean_names(
            self.emitter_types,
            "emitter_types",
            lowercase=True,
        )
        storage_types = _clean_names(
            self.storage_types,
            "storage_types",
            lowercase=True,
        )
        transport_type = _clean_name(
            self.transport_type,
            "transport_type",
        ).lower()

        role_types = set(emitter_types) | set(storage_types)

        if len(set(emitter_types)) != len(emitter_types):
            raise ValueError("emitter_types cannot contain duplicates")

        if len(set(storage_types)) != len(storage_types):
            raise ValueError("storage_types cannot contain duplicates")

        if set(emitter_types) & set(storage_types):
            raise ValueError(
                "emitter_types and storage_types must not overlap"
            )

        if transport_type in role_types:
            raise ValueError(
                "transport_type must differ from emitter and storage types"
            )

        if not isinstance(self.allow_transport_to_transport, bool):
            raise TypeError(
                "allow_transport_to_transport must be a boolean"
            )

        emitter_to_emitter_rule = _clean_name(
            self.emitter_to_emitter_rule,
            "emitter_to_emitter_rule",
        ).lower()

        if (
            emitter_to_emitter_rule
            not in SUPPORTED_EMITTER_TO_EMITTER_RULES
        ):
            raise ValueError(
                "emitter_to_emitter_rule must be one of "
                f"{sorted(SUPPORTED_EMITTER_TO_EMITTER_RULES)}"
            )

        emitter_to_emitter_max_detour_factor = float(
            self.emitter_to_emitter_max_detour_factor
        )

        if (
            not math.isfinite(emitter_to_emitter_max_detour_factor)
            or emitter_to_emitter_max_detour_factor < 1.0
        ):
            raise ValueError(
                "emitter_to_emitter_max_detour_factor must be finite "
                "and at least 1"
            )

        if isinstance(self.k, bool) or not isinstance(self.k, int):
            raise TypeError("k must be an integer")

        if self.k < 1:
            raise ValueError("k must be at least 1")

        relative_distance_factor = float(self.relative_distance_factor)

        if (
            not math.isfinite(relative_distance_factor)
            or relative_distance_factor < 1.0
        ):
            raise ValueError(
                "relative_distance_factor must be finite and at least 1"
            )

        max_distance_km = self.max_distance_km

        if max_distance_km is not None:
            max_distance_km = float(max_distance_km)

            if not math.isfinite(max_distance_km) or max_distance_km <= 0:
                raise ValueError(
                    "max_distance_km must be finite and positive when set"
                )

        object.__setattr__(self, "nodes_sheet", nodes_sheet)
        object.__setattr__(self, "mode_sheets", mode_sheets)
        object.__setattr__(self, "nodes_crs", nodes_crs)
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "emitter_types", emitter_types)
        object.__setattr__(self, "storage_types", storage_types)
        object.__setattr__(self, "transport_type", transport_type)
        object.__setattr__(
            self,
            "emitter_to_emitter_rule",
            emitter_to_emitter_rule,
        )
        object.__setattr__(
            self,
            "emitter_to_emitter_max_detour_factor",
            emitter_to_emitter_max_detour_factor,
        )
        object.__setattr__(
            self,
            "relative_distance_factor",
            relative_distance_factor,
        )
        object.__setattr__(self, "max_distance_km", max_distance_km)

def _clean_name(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")

    return value.strip()


def _clean_names(
    values: object,
    field_name: str,
    *,
    lowercase: bool = False,
) -> tuple[str, ...]:
    if isinstance(values, str):
        raise TypeError(
            f"{field_name} must be a sequence, not a single string"
        )

    try:
        items = tuple(values)  # type: ignore[arg-type]
    except TypeError as error:
        raise TypeError(f"{field_name} must be an iterable") from error

    if not items:
        raise ValueError(f"{field_name} must contain at least one value")

    cleaned = tuple(_clean_name(item, field_name) for item in items)

    if lowercase:
        cleaned = tuple(item.lower() for item in cleaned)

    return cleaned
