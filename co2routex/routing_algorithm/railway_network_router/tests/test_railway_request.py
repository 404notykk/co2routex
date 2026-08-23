from pathlib import Path
import tempfile
import unittest

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from railway_router.models import RailwayRequestSettings
from railway_router.railway_request import process_railway_request


def _write_workbook(path: Path, coordinates: list[tuple[float, float]]):
    node_ids = [f"n{index + 1}" for index in range(len(coordinates))]
    nodes = pd.DataFrame(
        {
            "node_id": node_ids,
            "node_name": node_ids,
            "longitude": [coordinate[0] for coordinate in coordinates],
            "latitude": [coordinate[1] for coordinate in coordinates],
            "altitude": [10] * len(node_ids),
            "annual_flux": [1] * len(node_ids),
            "node_type": ["emitter"] * len(node_ids),
        }
    )
    matrix = pd.DataFrame(0, index=node_ids, columns=node_ids)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        nodes.to_excel(writer, sheet_name="nodes", index=False)
        for sheet_name in ("pipeline", "truck", "railway"):
            matrix.to_excel(writer, sheet_name=sheet_name)


def _write_railway_database(root: Path):
    stations_path = root / "railway_stations_NL.gpkg"
    distances_path = root / "station_distances_NL.csv"
    stations = gpd.GeoDataFrame(
        {
            "station_id": ["a", "b"],
            "station_name": ["Alpha", "Beta"],
        },
        geometry=[Point(0, 0), Point(10_000, 0)],
        crs="EPSG:3857",
    )
    stations.to_file(stations_path, layer="stations", driver="GPKG")
    pd.DataFrame(
        {
            "from_station_id": ["a"],
            "to_station_id": ["b"],
            "distance_km": [12.5],
        }
    ).to_csv(distances_path, index=False)
    return stations_path, distances_path


class RailwayRequestTest(unittest.TestCase):
    def test_adds_stations_and_populates_railway_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook_path = root / "node_metrics.xlsx"
            _write_workbook(workbook_path, [(100, 0), (9_900, 0)])
            stations_path, distances_path = _write_railway_database(root)

            result = process_railway_request(
                RailwayRequestSettings(
                    requested=True,
                    workbook_path=workbook_path,
                    country_code="NL",
                    stations_path=stations_path,
                    stations_layer="stations",
                    distances_path=distances_path,
                    nodes_crs="EPSG:3857",
                )
            )

            self.assertEqual(result["selected_station_count"], 2)
            self.assertEqual(result["inserted_station_pairs"], 1)
            nodes = pd.read_excel(workbook_path, sheet_name="nodes", dtype=str)
            added = nodes[nodes["node_id"].str.startswith("rail_NL_")]
            self.assertEqual(set(added["node_type"]), {"transport"})
            railway = pd.read_excel(workbook_path, sheet_name="railway", index_col=0)
            self.assertAlmostEqual(railway.loc["rail_NL_a", "rail_NL_b"], 12.5)
            self.assertAlmostEqual(railway.loc["rail_NL_b", "rail_NL_a"], 12.5)
            truck = pd.read_excel(workbook_path, sheet_name="truck", index_col=0)
            self.assertEqual(truck.loc["n1", "rail_NL_a"], 0)

    def test_one_selected_station_leaves_railway_matrix_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook_path = root / "node_metrics.xlsx"
            _write_workbook(workbook_path, [(100, 0), (200, 0)])
            stations_path, distances_path = _write_railway_database(root)

            result = process_railway_request(
                RailwayRequestSettings(
                    requested=True,
                    workbook_path=workbook_path,
                    country_code="NL",
                    stations_path=stations_path,
                    stations_layer="stations",
                    distances_path=distances_path,
                    nodes_crs="EPSG:3857",
                )
            )

            self.assertEqual(result["selected_station_count"], 1)
            railway = pd.read_excel(workbook_path, sheet_name="railway", index_col=0)
            self.assertTrue((railway.fillna(0).to_numpy() == 0).all())

    def test_not_requested_leaves_workbook_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook_path = root / "node_metrics.xlsx"
            _write_workbook(workbook_path, [(100, 0)])
            original = workbook_path.read_bytes()

            result = process_railway_request(
                RailwayRequestSettings(
                    requested=False,
                    workbook_path=workbook_path,
                    country_code="NL",
                    stations_path=root / "missing.gpkg",
                    distances_path=root / "missing.csv",
                    nodes_crs="EPSG:3857",
                )
            )

            self.assertFalse(result["requested"])
            self.assertEqual(workbook_path.read_bytes(), original)

