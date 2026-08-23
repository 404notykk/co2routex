from pathlib import Path
import tempfile
import unittest

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

from railway_router.models import DistanceCalculationSettings
from railway_router.station_distances import calculate_station_distances


class StationDistanceTest(unittest.TestCase):
    def test_calculates_routes_from_existing_geopackages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            railway_path = root / "railway_NL.gpkg"
            stations_path = root / "railway_stations_NL.gpkg"
            routes_path = root / "station_routes_NL.gpkg"
            distances_path = root / "station_distances_NL.csv"

            railway = gpd.GeoDataFrame(
                {"line_id": [1]},
                geometry=[LineString([(0, 0), (5_000, 0), (10_000, 0)])],
                crs="EPSG:3857",
            )
            stations = gpd.GeoDataFrame(
                {
                    "station_id": ["a", "b"],
                    "station_name": ["Alpha", "Beta"],
                },
                geometry=[Point(0, 50), Point(10_000, 50)],
                crs="EPSG:3857",
            )
            railway.to_file(railway_path, layer="railway", driver="GPKG")
            stations.to_file(stations_path, layer="stations", driver="GPKG")

            result = calculate_station_distances(
                DistanceCalculationSettings(
                    country_code="NL",
                    railway_path=railway_path,
                    railway_layer="railway",
                    stations_path=stations_path,
                    stations_layer="stations",
                    output_routes_path=routes_path,
                    output_distances_path=distances_path,
                    routing_crs="EPSG:3857",
                )
            )

            self.assertEqual(result["station_count"], 2)
            self.assertEqual(result["connected_station_pairs"], 1)
            distances = pd.read_csv(distances_path)
            self.assertAlmostEqual(distances.loc[0, "distance_km"], 10.0)
            routes = gpd.read_file(routes_path, layer="station_routes")
            self.assertEqual(len(routes), 1)
            self.assertAlmostEqual(routes.loc[0, "geometry"].length, 10_000.0)

