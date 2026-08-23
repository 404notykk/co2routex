"""Cost-model orchestration."""

from __future__ import annotations

import logging
from pathlib import Path

from .config import TransportCostConfig
from .workbook import read_coefficients, write_output


LOGGER = logging.getLogger(__name__)
SUPPORTED_MODES = ("truck", "railway")


def run(config: TransportCostConfig, modes: tuple[str, ...] = SUPPORTED_MODES) -> Path:
    normalized = tuple(mode.lower() for mode in modes)
    if not normalized or any(mode not in SUPPORTED_MODES for mode in normalized):
        raise ValueError("modes must contain truck, railway, or both")
    if len(set(normalized)) != len(normalized):
        raise ValueError("modes cannot contain duplicates")

    node_ids_by_mode: dict[str, list[str]] = {}
    coefficients = []
    for mode in normalized:
        node_ids, mode_coefficients = read_coefficients(config, mode)
        node_ids_by_mode[mode] = node_ids
        coefficients.extend(mode_coefficients)
        LOGGER.info("Calculated %d %s coefficients", len(mode_coefficients), mode)
    return write_output(config, normalized, node_ids_by_mode, coefficients)

