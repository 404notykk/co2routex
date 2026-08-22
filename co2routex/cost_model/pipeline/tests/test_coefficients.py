from pathlib import Path

from pipeline_cost_model.config import PipelineCostConfig
from pipeline_cost_model.cost_model import PipelineCostModel
from pipeline_cost_model.models import EndpointCost, RouteInput


class StubPipelineCostModel(PipelineCostModel):
    def _calculate_endpoint(self, length_km, capacity_t_per_h):
        return EndpointCost(
            capacity_t_per_h=capacity_t_per_h,
            capex_pipeline_eur=1000.0 + 2.0 * capacity_t_per_h,
            capex_compression_eur=500.0 + 3.0 * capacity_t_per_h,
            capex_total_eur=1500.0 + 5.0 * capacity_t_per_h,
            selected_inner_diameter_m=None,
            selected_outer_diameter_m=None,
            steel_grade=None,
            number_of_pumps=None,
            specific_compression_energy_mwh_per_t=None,
        )


def test_baseline_and_spatial_coefficients(tmp_path: Path):
    workbook = tmp_path / "input.xlsx"
    workbook.touch()
    config = PipelineCostConfig(workbook_path=workbook)
    result = StubPipelineCostModel(config).calculate_route(
        RouteInput("A", "B", 40.0, 2.5)
    )
    assert result.capex_baseline_slope_eur_per_t_per_h == 5.0
    assert result.capex_baseline_intercept_eur == 1500.0
    assert result.capex_spatial_slope_eur_per_t_per_h == 12.5
    assert result.capex_spatial_intercept_eur == 3750.0
