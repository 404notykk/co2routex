"""Cost-model orchestration."""

from __future__ import annotations

import logging
from pathlib import Path

from .config import TransportCostConfig
from .config import normalize_mode
from .inputs import read_routes
from .cost_model import calculate_route
from .models import RouteCostResult
from .outputs import write_results
from .currency import conversion_metadata


LOGGER = logging.getLogger(__name__)
SUPPORTED_MODES = ("truck", "railway")


def run_with_outputs(config: TransportCostConfig, modes: tuple[str, ...] = SUPPORTED_MODES) -> dict[str, Path]:
    config.validate()
    normalized = tuple(normalize_mode(mode) for mode in modes)
    if not normalized:
        raise ValueError("modes must contain truck, railway, or both")
    if len(set(normalized)) != len(normalized):
        raise ValueError("modes cannot contain duplicates")
    LOGGER.info("EUR 2021 -> EUR 2024 factor %.12g; %s", config.conversion_factor,
                conversion_metadata(config)["conversion_basis"])

    data = read_routes(config, normalized)
    results = []
    for route in data.routes:
        try:
            result = calculate_route(route, config)
        except MemoryError:
            raise
        except Exception as exc:
            result = RouteCostResult(route, cost_error=f"{type(exc).__name__}: {exc}")
            LOGGER.error("%s %s -> %s, data row %s, cell %s: %s", route.mode, route.from_id,
                         route.to_id, route.source_row, route.source_cell, result.cost_error)
        results.append(result)
    outputs = write_results(config, normalized, data, results)
    failed = sum(r.cost_status == "failed" for r in results)
    with_annual = sum(r.annual_cost_eur_per_year is not None for r in results)
    LOGGER.info("%s successful, %s failed; %s annual totals", len(results) - failed, failed, with_annual)
    if failed:
        LOGGER.warning("Failed routes have blank costs; use only cost_status=ok rows")
    for role, path in outputs.items():
        LOGGER.info("%s: %s", role, path)
    return outputs


def run(config: TransportCostConfig, modes: tuple[str, ...] = SUPPORTED_MODES) -> Path:
    """Return the costed input path for compatibility; use run_with_outputs for both paths."""
    outputs = run_with_outputs(config, modes)
    return outputs.get("costed_input", outputs.get("report"))
