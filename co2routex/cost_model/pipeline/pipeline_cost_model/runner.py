"""Pipeline cost-model orchestration."""

from __future__ import annotations

import logging
from pathlib import Path

from .config import PipelineCostConfig
from .cost_model import PipelineCostModel
from .inputs import read_routes
from .outputs import write_results


LOGGER = logging.getLogger(__name__)


def run(config: PipelineCostConfig) -> tuple[Path, Path]:
    routes = read_routes(config.workbook_path, config.route_metrics_sheet)
    model = PipelineCostModel(config)
    results = []
    for number, route in enumerate(routes, start=1):
        LOGGER.info("Costing route %s/%s: %s -> %s", number, len(routes), route.from_id, route.to_id)
        results.append(model.calculate_route(route))
    return write_results(config, results)
