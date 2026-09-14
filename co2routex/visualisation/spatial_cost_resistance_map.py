from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.plot import plotting_extent


# Change this to the location of your exported GeoTIFF
tiff_path = Path("../database/pipeline/spatial_cost_resistance/SCRM_100_NL.tif")


with rasterio.open(tiff_path) as src:
    multiplier = src.read(1, masked=True)
    extent = plotting_extent(src)

    print(f"File: {tiff_path}")
    print(f"CRS: {src.crs}")
    print(f"Cell size: {src.res}")
    print(f"Raster size: {src.width} × {src.height}")
    print(f"NoData value: {src.nodata}")
    print(f"Minimum multiplier: {multiplier.min()}")
    print(f"Maximum multiplier: {multiplier.max()}")

# Ensure invalid and NoData cells are transparent
multiplier = np.ma.masked_invalid(multiplier)

figure, axis = plt.subplots(figsize=(11, 10))

image = axis.imshow(
    multiplier,
    cmap="viridis",
    extent=extent,
    origin="upper",
    interpolation="nearest",
)

colorbar = figure.colorbar(image, ax=axis, shrink=0.8)
colorbar.set_label("Integrated multiplier")

axis.set_title("Integrated Spatial Multiplier Map")
axis.set_xlabel("Easting (m)")
axis.set_ylabel("Northing (m)")
axis.set_aspect("equal")

plt.tight_layout()
plt.show()