"""Convenience API for one distance and one annual transported quantity."""
from dataclasses import replace
from .config import PipelineCostConfig
from .annual_costs import PipelineCostModel
from .models import RouteCostResult, RouteInput


def calculate_pipeline_cost(distance_km: float, annual_flow_t_per_year: float, *,
                            average_route_resistance: float = 1.0,
                            config: PipelineCostConfig | None = None) -> RouteCostResult:
    """Return fixed-design annual coefficients and the detailed reference costs.

    Defaults are the adopted model assumptions, including 8000 operating hours.
    Pass a PipelineCostConfig to change hours, electricity price, terrain, etc.
    No fitting bounds, sample counts or fitting method are required.
    average_route_resistance is retained only as routing metadata.
    """
    settings = replace(config or PipelineCostConfig(), mode="both", coefficient_method="fixed_design")
    route = RouteInput("from", "to", distance_km, average_route_resistance,
                       "single_case", annual_flow_t_per_year)
    return PipelineCostModel(settings).calculate_route(route)
