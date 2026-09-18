"""
Extract the main OSM road network for truck routing.

Included OSM highway classes:
- motorway
- motorway_link
- trunk
- trunk_link
- primary
- primary_link
"""

from pathlib import Path
import shutil
import subprocess


# ------------------------------------------------------------------
# COUNTRY IDENTIFICATION
# ------------------------------------------------------------------

COUNTRY_CODES = {
    "germany": "DE",
    "netherlands": "NL",
    "norway": "NO",
}


# ------------------------------------------------------------------
# TRUCK-NETWORK FILTER
# ------------------------------------------------------------------

# Link roads are included to preserve network connectivity at junctions.
TRUCK_NETWORK_FILTER = (
    "w/highway="
    "motorway,motorway_link,"
    "trunk,trunk_link,"
    "primary,primary_link"
)


# ------------------------------------------------------------------
# DIRECTORIES
# ------------------------------------------------------------------

# Directory containing the country-level .osm.pbf files.
INPUT_DIR = Path(".")

# Directory for the resulting truck-network GeoPackages.
OUTPUT_DIR = Path("processed_osm")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------
# CHECK REQUIRED COMMAND-LINE TOOLS
# ------------------------------------------------------------------

for executable in ("osmium", "ogr2ogr"):
    if shutil.which(executable) is None:
        raise RuntimeError(
            f"Required command-line tool not found: {executable}"
        )


# ------------------------------------------------------------------
# FIND INPUT FILES
# ------------------------------------------------------------------

osm_files = sorted(INPUT_DIR.glob("*.osm.pbf"))

if not osm_files:
    raise FileNotFoundError(
        f"No .osm.pbf files were found in: {INPUT_DIR.resolve()}"
    )


# ------------------------------------------------------------------
# PROCESS EACH COUNTRY FILE
# ------------------------------------------------------------------

for osm_file in osm_files:

    filename_lower = osm_file.name.lower()
    country_code = None

    for country_name, code in COUNTRY_CODES.items():
        if country_name in filename_lower:
            country_code = code
            break

    if country_code is None:
        print(
            f"Skipping {osm_file.name}: "
            "country could not be identified from the filename."
        )
        continue

    print(f"\nProcessing {osm_file.name} ({country_code})")

    subset_pbf = OUTPUT_DIR / f"{country_code}_truck_network.osm.pbf"
    gpkg_file = OUTPUT_DIR / f"{country_code}_truck_network.gpkg"

    # --------------------------------------------------------------
    # EXTRACT THE TRUCK ROAD NETWORK
    # --------------------------------------------------------------

    print("  Extracting truck network...")

    subprocess.run(
        [
            "osmium",
            "tags-filter",
            str(osm_file),
            TRUCK_NETWORK_FILTER,
            "-o",
            str(subset_pbf),
            "--overwrite",
            "--progress",
        ],
        check=True,
    )

    # --------------------------------------------------------------
    # CONVERT TO GEOPACKAGE
    # --------------------------------------------------------------

    print(f"  Creating {gpkg_file.name}...")

    subprocess.run(
        [
            "ogr2ogr",
            "-overwrite",
            "-f",
            "GPKG",
            str(gpkg_file),
            str(subset_pbf),
            "lines",
            "-nln",
            "truck_network",
            "-lco",
            "SPATIAL_INDEX=YES",
        ],
        check=True,
    )

    # --------------------------------------------------------------
    # REMOVE TEMPORARY PBF
    # --------------------------------------------------------------

    subset_pbf.unlink(missing_ok=True)

    print(f"  Finished: {gpkg_file}")


print("\nAll truck-network processing completed.")