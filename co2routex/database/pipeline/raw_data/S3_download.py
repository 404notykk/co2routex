import boto3
import os
import re
from pathlib import Path

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

ACCESS_KEY = "XXX"
SECRET_KEY = "XXX"

# Europe bounding box
MIN_LON = -32.0
MAX_LON = 45.0
MIN_LAT = 27.0
MAX_LAT = 72.0

YEAR = 2020
OUTPUT_DIR = "./land_cover_download"

S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"
BUCKET = "eodata"

DATE_PREFIX = (
    f"CLMS/landcover_landuse/dynamic_land_cover/"
    f"lcm_global_10m_yearly_v1/{YEAR}/01/01/"
)

TILE_SIZE_DEG = 3.0

# ─── CONNECT TO S3 ────────────────────────────────────────────────────────────

print("Connecting to Copernicus S3...")

s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    region_name="default"
)

# ─── TILE FUNCTIONS ───────────────────────────────────────────────────────────

def parse_tile_from_name(name):
    """
    Parse tile code from folder or file names like:
    LCFM_LCM-10_V100_2020_N45E006_cog/
    LCFM_LCM-10_V100_2020_N45E006_MAP.tif
    """

    match = re.search(r'_([NS])(\d{2})([EW])(\d{3})(?:_|/)', name)

    if not match:
        return None

    lat_hem, lat_num, lon_hem, lon_num = match.groups()

    lat = int(lat_num)
    lon = int(lon_num)

    if lat_hem == "S":
        lat = -lat

    if lon_hem == "W":
        lon = -lon

    return float(lat), float(lon)


def tile_overlaps_bbox(tile_prefix, min_lon, max_lon, min_lat, max_lat):
    parsed = parse_tile_from_name(tile_prefix)

    if parsed is None:
        return False

    tile_min_lat, tile_min_lon = parsed
    tile_max_lat = tile_min_lat + TILE_SIZE_DEG
    tile_max_lon = tile_min_lon + TILE_SIZE_DEG

    return (
        tile_min_lon < max_lon and
        tile_max_lon > min_lon and
        tile_min_lat < max_lat and
        tile_max_lat > min_lat
    )


# ─── STEP 1: LIST TILE FOLDERS ────────────────────────────────────────────────

print("Listing tile folders under:")
print(DATE_PREFIX)

tile_prefixes = []

paginator = s3.get_paginator("list_objects_v2")

for page in paginator.paginate(
    Bucket=BUCKET,
    Prefix=DATE_PREFIX,
    Delimiter="/"
):
    for cp in page.get("CommonPrefixes", []):
        tile_prefixes.append(cp["Prefix"])

print(f"Found {len(tile_prefixes)} tile folders.")

if len(tile_prefixes) == 0:
    print("No tile folders found. Check DATE_PREFIX and credentials.")
    raise SystemExit


# ─── STEP 2: FILTER TILE FOLDERS BY BBOX ──────────────────────────────────────

selected_tile_prefixes = [
    p for p in tile_prefixes
    if tile_overlaps_bbox(p, MIN_LON, MAX_LON, MIN_LAT, MAX_LAT)
]

print(f"Tile folders overlapping your region: {len(selected_tile_prefixes)}")

for p in selected_tile_prefixes[:20]:
    print("  ", p)

if len(selected_tile_prefixes) > 20:
    print(f"  ... and {len(selected_tile_prefixes) - 20} more")


# ─── STEP 3: FIND TIF INSIDE EACH SELECTED TILE FOLDER ────────────────────────

selected_tifs = []

for tile_prefix in selected_tile_prefixes:
    response = s3.list_objects_v2(
        Bucket=BUCKET,
        Prefix=tile_prefix
    )

    for obj in response.get("Contents", []):
        key = obj["Key"]

        if key.endswith("_MAP.tif") or key.endswith(".tif") or key.endswith(".tiff"):
            selected_tifs.append(key)

print(f"GeoTIFF files to download: {len(selected_tifs)}")

for key in selected_tifs[:20]:
    print("  ", key)

if len(selected_tifs) > 20:
    print(f"  ... and {len(selected_tifs) - 20} more")


# ─── STEP 4: DOWNLOAD ─────────────────────────────────────────────────────────

Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

for i, key in enumerate(selected_tifs, start=1):
    filename = os.path.basename(key)
    local_path = os.path.join(OUTPUT_DIR, filename)

    if os.path.exists(local_path):
        print(f"[{i}/{len(selected_tifs)}] Already exists, skipping: {filename}")
        continue

    try:
        head = s3.head_object(Bucket=BUCKET, Key=key)
        size_mb = head["ContentLength"] / (1024 * 1024)
        size_str = f"{size_mb:.1f} MB"
    except Exception:
        size_str = "unknown size"

    print(f"[{i}/{len(selected_tifs)}] Downloading {filename} ({size_str})")

    s3.download_file(BUCKET, key, local_path)

    print(f"    Saved to {local_path}")

print("\nAll downloads complete.")
print(f"Files saved in: {os.path.abspath(OUTPUT_DIR)}")