"""Run direct benchmarks, annual-flow approximations, or both."""
from __future__ import annotations
import logging
from .cost_model import PipelineCostModel
from .inputs import read_routes
from .models import RouteCostResult
from .outputs import write_results
from .currency import conversion_metadata

LOGGER = logging.getLogger(__name__)

def run(config):
    config.validate()
    conversion = conversion_metadata(config)
    LOGGER.info("EUR 2024 export factor: %.12g. %s; electricity input assumed EUR 2021/MWh.",
                config.conversion_factor, conversion["conversion_basis"])
    routes = read_routes(config)
    model = PipelineCostModel(config)
    results = []
    for number, route in enumerate(routes, start=1):
        LOGGER.info("Costing %s/%s: %s (%s -> %s), input data row %s", number, len(routes), route.scenario_id, route.from_id, route.to_id,
                    route.source_row - 1 if route.source_row is not None else "not supplied")
        try:
            result = model.calculate_route(route)
        except MemoryError:
            # A resource failure affects the whole run, rather than one route.
            raise
        except Exception as exc:
            # Only the calculation of this route is recoverable here. Input,
            # configuration and export errors remain fatal. KeyboardInterrupt
            # and SystemExit are BaseExceptions and are never swallowed.
            message = f"{type(exc).__name__}: {exc}"
            result = RouteCostResult(route, error_message=message)
            LOGGER.error("Failed %s (%s -> %s), input data row %s: %s. Continuing with remaining routes.",
                         route.scenario_id, route.from_id, route.to_id,
                         route.source_row - 1 if route.source_row is not None else "not supplied",
                         message)
        results.append(result)
        if result.approximation:
            for warning in result.approximation.warnings:
                LOGGER.warning("%s: %s", route.scenario_id, warning)
    outputs = write_results(config, results)
    failed = sum(result.cost_status == "failed" for result in results)
    LOGGER.info("Results written: %s successful, %s failed, %s routes attempted.",
                len(results) - failed, failed, len(results))
    if failed:
        LOGGER.warning("Failed routes have blank costs. Review cost_status/cost_error in the report summary "
                       "or pipeline_cost_status/pipeline_cost_error in the costed input.")
    return outputs
