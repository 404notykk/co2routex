"""
Extract two OSM truck-road network variants for NL, DE and NO only.

Without secondary: motorway, trunk, primary, and their *_link roads.
With secondary: all the above plus secondary and secondary_link.

Inputs: database/pipeline/raw/existing_network/*.osm.pbf
Outputs for each country, in database/container_based_truck/:
    {country}_truck_network_without_secondary.gpkg
    {country}_truck_network_without_secondary.parquet
    {country}_truck_network_with_secondary.gpkg
    {country}_truck_network_with_secondary.parquet

The GeoPackage layer is named "truck_network". GeoParquet preserves the same
features, attributes and CRS. Parquet compression is disabled by default.

Requirements:
    Command-line tools: osmium-tool and GDAL (ogr2ogr).
    Python packages: python -m pip install geopandas pyogrio pyarrow

Place this script in data_processing/container_based_network/.
Paths are resolved relative to the script location, independent of PyCharm's
working directory. The output directory is created automatically.
Each run replaces the outputs with these same variant names.
GeoParquet conversion loads one country's variant into memory at a time.
The full source PBF is filtered once per country; both variants are generated
from that smaller subset. Only the requested highway classes are exported.
This is a road extraction; graph construction and access/turn-restriction
handling remain the router's responsibility.

GeoParquet writer documentation:
https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoDataFrame.to_parquet.html
"""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile


# ------------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------------

COUNTRY_CODES = {
    "germany": "DE",
    "netherlands": "NL",
    "norway": "NO",
}

# Include link roads in both variants to preserve connections at junctions.
MAIN_ROAD_TYPES = (
    "motorway", "motorway_link",
    "trunk", "trunk_link",
    "primary", "primary_link",
)
ROAD_VARIANTS = {
    "without_secondary": MAIN_ROAD_TYPES,
    "with_secondary": MAIN_ROAD_TYPES + ("secondary", "secondary_link"),
}
TRUCK_NETWORK_FILTER = "w/highway=" + ",".join(ROAD_VARIANTS["with_secondary"])

# The package directory contains the sibling data_processing/ and database/
# directories shown in the project layout.
PROJECT_DIR = Path(__file__).resolve().parents[2]
INPUT_DIR = PROJECT_DIR / "database" / "pipeline" / "raw" / "existing_network"
OUTPUT_DIR = PROJECT_DIR / "database" / "container_based_truck"

GPKG_LAYER_NAME = "truck_network"
# Lossless compression affects disk storage and I/O, not road accuracy.
# "zstd": compact files; "snappy": alternative favouring compression speed;
# None: no compression. The fastest option depends on data and storage.
PARQUET_COMPRESSION = None


def main():
    # Optional user-local tool installation, also discoverable from PyCharm.
    # An explicit OSM_TOOLS_BIN environment variable can override this directory.
    tools_dir = Path(os.environ.get(
        "OSM_TOOLS_BIN", str(Path.home() / ".local/share/osm-tools/bin")
    )).expanduser()
    if tools_dir.is_dir():
        os.environ["PATH"] = str(tools_dir) + os.pathsep + os.environ.get("PATH", "")

    # Check dependencies before running the potentially long extraction.
    for executable in ("osmium", "ogr2ogr"):
        if shutil.which(executable) is None:
            raise RuntimeError(
                f"Required command-line tool not found: {executable}. "
                "Install osmium-tool and GDAL in ~/.local/share/osm-tools, or set OSM_TOOLS_BIN to their bin directory."
            )

    try:
        import geopandas as gpd
        import pyarrow as pa
        import pyogrio  # Required by the read_file engine below.
    except ImportError as exc:
        raise RuntimeError(
            "Missing Python dependencies. Install them in the environment "
            "running this script: python -m pip install geopandas pyogrio pyarrow"
        ) from exc

    if PARQUET_COMPRESSION is not None and not pa.Codec.is_available(PARQUET_COMPRESSION):
        raise RuntimeError(
            f"PyArrow does not support the requested codec: {PARQUET_COMPRESSION}"
        )

    osm_files = sorted(INPUT_DIR.glob("*.osm.pbf"))
    if not osm_files:
        raise FileNotFoundError(
            f"No .osm.pbf files were found in: {INPUT_DIR.resolve()}"
        )

    # Resolve all countries first to prevent two snapshots from silently
    # replacing the same country's outputs during a single run.
    country_inputs = {}
    for osm_file in osm_files:
        country_code = next(
            (code for name, code in COUNTRY_CODES.items()
             if name in osm_file.name.lower()),
            None,
        )
        if country_code is None:
            print(f"Skipping {osm_file.name}: not a recognised NL, DE or NO input.")
            continue
        if country_code in country_inputs:
            raise ValueError(
                f"Multiple inputs found for {country_code}: "
                f"{country_inputs[country_code].name} and {osm_file.name}. "
                "Keep one input snapshot per country in INPUT_DIR."
            )
        country_inputs[country_code] = osm_file

    if not country_inputs:
        raise FileNotFoundError("No recognised country .osm.pbf files found.")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for country_code, osm_file in country_inputs.items():
        print(f"\nProcessing {osm_file.name} ({country_code})", flush=True)

        # Prepare all four outputs before publishing them. Temporary files are
        # removed automatically even if a command or conversion fails.
        with tempfile.TemporaryDirectory(prefix=f"{country_code}_roads_",
                                         dir=OUTPUT_DIR) as tmp:
            temp_dir = Path(tmp)
            subset_pbf = temp_dir / "roads_with_secondary.osm.pbf"
            pending_outputs = []

            print("  Extracting main and secondary roads from source PBF...", flush=True)
            subprocess.run(
                ["osmium", "tags-filter", str(osm_file),
                 TRUCK_NETWORK_FILTER, "-o", str(subset_pbf), "--progress"],
                check=True,
            )

            for variant, road_types in ROAD_VARIANTS.items():
                output_stem = f"{country_code}_truck_network_{variant}"
                temp_gpkg = temp_dir / f"{output_stem}.gpkg"
                temp_parquet = temp_dir / f"{output_stem}.parquet"
                # Values are fixed highway classes defined above.
                where_clause = "highway IN (" + ",".join(
                    f"'{road_type}'" for road_type in road_types
                ) + ")"

                print(f"  Creating {temp_gpkg.name}...", flush=True)
                subprocess.run(
                    ["ogr2ogr", "-f", "GPKG", str(temp_gpkg), str(subset_pbf),
                     "lines", "-nln", GPKG_LAYER_NAME, "-where", where_clause,
                     "-lco", "SPATIAL_INDEX=YES"],
                    check=True,
                )

                print(f"  Creating {temp_parquet.name}...", flush=True)
                roads = gpd.read_file(
                    temp_gpkg, layer=GPKG_LAYER_NAME,
                    engine="pyogrio", use_arrow=True,
                )
                if roads.empty:
                    raise RuntimeError(
                        f"No road features extracted for {country_code}/{variant}."
                    )
                if roads.crs is None:
                    raise RuntimeError(f"Extracted roads have no CRS: {temp_gpkg}")

                roads.to_parquet(
                    temp_parquet, compression=PARQUET_COMPRESSION, index=False,
                )
                print(f"  {variant}: {len(roads):,} features; CRS: {roads.crs}")
                del roads
                pending_outputs.extend((temp_gpkg, temp_parquet))

            for temp_file in pending_outputs:
                output_file = OUTPUT_DIR / temp_file.name
                temp_file.replace(output_file)
                size_mb = output_file.stat().st_size / (1024 ** 2)
                print(f"  Saved: {output_file} ({size_mb:,.1f} MiB)")

    print("\nAll truck-network processing completed.")


if __name__ == "__main__":
    main()
