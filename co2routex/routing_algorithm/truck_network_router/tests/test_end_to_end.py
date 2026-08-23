from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import LineString

from truck_router.config import load_settings
from truck_router.workflow import run


def test_router_updates_only_truck_metrics_and_writes_routes(tmp_path: Path):
    network_path = tmp_path / "NL_truck_network.gpkg"
    roads = gpd.GeoDataFrame(
        {
            "highway": ["primary"],
            "oneway": ["no"],
            "geometry": [LineString([(0, 0), (1000, 0)])],
        },
        crs="EPSG:3857",
    )
    roads.to_file(network_path, layer="truck_network", driver="GPKG")

    workbook_path = tmp_path / "node_metrics.xlsx"
    nodes = pd.DataFrame(
        [
            [1, "emitter", 200, 100, 10, 1.0, "cement", "NL"],
            [2, "storage", 800, 100, 10, None, "storage", "NL"],
        ],
        columns=[
            "node_id",
            "node_name",
            "longitude",
            "latitude",
            "altitude",
            "annual_flux",
            "node_type",
            "country_code",
        ],
    )
    original_pipeline = pd.DataFrame([[0, 12.3], [0, 0]], index=[1, 2], columns=[1, 2])
    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        nodes.to_excel(writer, sheet_name="nodes", index=False)
        original_pipeline.to_excel(writer, sheet_name="pipeline")
        original_pipeline.to_excel(writer, sheet_name="truck")

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "workbook_path": str(workbook_path),
                "nodes_crs": "EPSG:3857",
                "working_crs": "EPSG:3857",
                "output_crs": "EPSG:4326",
                "networks": {
                    "NL": {"path": str(network_path), "layer": "truck_network"}
                },
                "routing": {
                    "maximum_snap_distance_m": 500,
                    "respect_oneway": True,
                    "include_snap_distance_in_metric": True,
                },
                "connection_rules": {
                    "policy": "directed_chain",
                    "candidate_source": "generated",
                },
                "outputs": {"directory": str(tmp_path / "output")},
            }
        ),
        encoding="utf-8",
    )

    result = run(load_settings(config_path))
    truck = pd.read_excel(workbook_path, sheet_name="truck", index_col=0)
    pipeline = pd.read_excel(workbook_path, sheet_name="pipeline", index_col=0)

    assert truck.loc[1, 2] == pytest.approx(0.8)
    assert truck.loc[2, 1] == 0
    assert pipeline.loc[1, 2] == pytest.approx(12.3)
    assert result["routes_path"].exists()
    assert result["summary_path"].exists()
