import boto3

ACCESS_KEY = "XXX"
SECRET_KEY = "XXX"

S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"
BUCKET = "eodata"

s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    region_name="default"
)

# ── Step 1: List top-level folders ──────────────────────────────────────────
print("=== Top-level folders in eodata bucket ===")
response = s3.list_objects_v2(Bucket=BUCKET, Prefix="", Delimiter="/")
for prefix in response.get("CommonPrefixes", []):
    print(" ", prefix["Prefix"])

# ── Step 2: Explore the CLMS folder ─────────────────────────────────────────
print("\n=== Contents of CLMS/ ===")
response = s3.list_objects_v2(Bucket=BUCKET, Prefix="CLMS/", Delimiter="/")
for prefix in response.get("CommonPrefixes", []):
    print(" ", prefix["Prefix"])

# ── Step 3: Go deeper ────────────────────────────────────────────────────────
print("\n=== Contents of CLMS/Global/ or similar ===")
for top in ["CLMS/Global/", "CLMS/landcover_landuse/", "CLMS/global/", "CLMS/LandCover/"]:
    r = s3.list_objects_v2(Bucket=BUCKET, Prefix=top, Delimiter="/")
    prefixes = r.get("CommonPrefixes", [])
    if prefixes:
        print(f"\n  Found under {top}:")
        for p in prefixes:
            print("   ", p["Prefix"])

# ── Step 4: Explore what is inside dynamic_land_cover/ ──────────────────────
print("\n=== Contents of CLMS/landcover_landuse/dynamic_land_cover/ ===")

TARGET = "CLMS/landcover_landuse/dynamic_land_cover/"

r = s3.list_objects_v2(
    Bucket=BUCKET,
    Prefix=TARGET,
    Delimiter="/"
)

print("\nSubfolders:")
for prefix in r.get("CommonPrefixes", []):
    print(" ", prefix["Prefix"])

print("\nFiles:")
for obj in r.get("Contents", []):
    if obj["Key"] != TARGET:
        print(" ", obj["Key"], f"  [{obj['Size'] / 1e6:.1f} MB]")

if not r.get("CommonPrefixes") and not r.get("Contents"):
    print("  (nothing found at that path)")

# ── Step 5: Explore lcm_global_10m_yearly_v1 ────────────────────────────────
print("\n=== Contents of CLMS/landcover_landuse/dynamic_land_cover/lcm_global_10m_yearly_v1/ ===")

TARGET = "CLMS/landcover_landuse/dynamic_land_cover/lcm_global_10m_yearly_v1/"

r = s3.list_objects_v2(
    Bucket=BUCKET,
    Prefix=TARGET,
    Delimiter="/"
)

print("\nSubfolders:")
for prefix in r.get("CommonPrefixes", []):
    print(" ", prefix["Prefix"])

print("\nFiles:")
for obj in r.get("Contents", []):
    if obj["Key"] != TARGET:
        print(" ", obj["Key"], f"  [{obj['Size'] / 1e6:.1f} MB]")

if not r.get("CommonPrefixes") and not r.get("Contents"):
    print("  (nothing found at that path)")

# ── Step 6: Explore 2020 folder ─────────────────────────────────────────────
print("\n=== Contents of CLMS/landcover_landuse/dynamic_land_cover/lcm_global_10m_yearly_v1/2020/ ===")

TARGET = "CLMS/landcover_landuse/dynamic_land_cover/lcm_global_10m_yearly_v1/2020/"

r = s3.list_objects_v2(
    Bucket=BUCKET,
    Prefix=TARGET,
    Delimiter="/"
)

print("\nSubfolders:")
for prefix in r.get("CommonPrefixes", []):
    print(" ", prefix["Prefix"])

print("\nFiles:")
for obj in r.get("Contents", []):
    if obj["Key"] != TARGET:
        print(" ", obj["Key"], f"  [{obj['Size'] / 1e6:.1f} MB]")

if not r.get("CommonPrefixes") and not r.get("Contents"):
    print("  (nothing found at that path)")

# ── Step 6: Explore 2020/01 folder ──────────────────────────────────────────
print("\n=== Contents of CLMS/landcover_landuse/dynamic_land_cover/lcm_global_10m_yearly_v1/2020/01/ ===")

TARGET = "CLMS/landcover_landuse/dynamic_land_cover/lcm_global_10m_yearly_v1/2020/01/"

r = s3.list_objects_v2(
    Bucket=BUCKET,
    Prefix=TARGET,
    Delimiter="/"
)

print("\nSubfolders:")
for prefix in r.get("CommonPrefixes", []):
    print(" ", prefix["Prefix"])

print("\nFiles:")
for obj in r.get("Contents", []):
    if obj["Key"] != TARGET:
        print(" ", obj["Key"], f"  [{obj['Size'] / 1e6:.1f} MB]")

if not r.get("CommonPrefixes") and not r.get("Contents"):
    print("  (nothing found at that path)")

# ── Step 7: Explore 2020/01/01 folder ───────────────────────────────────────
print("\n=== Contents of CLMS/landcover_landuse/dynamic_land_cover/lcm_global_10m_yearly_v1/2020/01/01/ ===")

TARGET = "CLMS/landcover_landuse/dynamic_land_cover/lcm_global_10m_yearly_v1/2020/01/01/"

r = s3.list_objects_v2(
    Bucket=BUCKET,
    Prefix=TARGET,
    Delimiter="/"
)

print("\nSubfolders:")
for prefix in r.get("CommonPrefixes", []):
    print(" ", prefix["Prefix"])

print("\nFiles:")
for obj in r.get("Contents", []):
    if obj["Key"] != TARGET:
        print(" ", obj["Key"], f"  [{obj['Size'] / 1e6:.1f} MB]")

if not r.get("CommonPrefixes") and not r.get("Contents"):
    print("  (nothing found at that path)")

# ── Step 8: Check what is inside the top 10 tile subfolders ─────────────────
print("\n=== Checking inside the top 10 subfolders under 2020/01/01/ ===")

BASE = "CLMS/landcover_landuse/dynamic_land_cover/lcm_global_10m_yearly_v1/2020/01/01/"

# First, get the tile subfolders under 2020/01/01/
r = s3.list_objects_v2(
    Bucket=BUCKET,
    Prefix=BASE,
    Delimiter="/"
)

tile_folders = [p["Prefix"] for p in r.get("CommonPrefixes", [])]

print(f"\nFound {len(tile_folders)} tile subfolders.")
print("Now checking the first 10:\n")

for i, tile_folder in enumerate(tile_folders[:10], start=1):
    print("=" * 100)
    print(f"[{i}/10] Contents of {tile_folder}")
    print("=" * 100)

    r2 = s3.list_objects_v2(
        Bucket=BUCKET,
        Prefix=tile_folder,
        Delimiter="/"
    )

    print("\nSubfolders:")
    subfolders = r2.get("CommonPrefixes", [])
    if subfolders:
        for prefix in subfolders:
            print(" ", prefix["Prefix"])
    else:
        print("  No subfolders found")

    print("\nFiles:")
    files = r2.get("Contents", [])
    file_count = 0

    for obj in files:
        if obj["Key"] != tile_folder:
            file_count += 1
            print(" ", obj["Key"], f"  [{obj['Size'] / 1e6:.1f} MB]")

    if file_count == 0:
        print("  No files found directly inside this folder")

    print()