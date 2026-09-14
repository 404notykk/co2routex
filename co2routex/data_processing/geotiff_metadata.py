#!/usr/bin/env python3
"""Display detailed metadata for a GeoTIFF.

Change GEOTIFF_PATH below and run this file directly in PyCharm.

Dependency:
    pip install rasterio
"""

from pathlib import Path
import re

try:
    import rasterio
except ImportError as exc:
    raise SystemExit(
        "Rasterio is required. Install it with: pip install rasterio"
    ) from exc


# =============================================================================
# USER SETTINGS
# =============================================================================

GEOTIFF_PATH = Path("../database/pipeline/spatial_cost_resistance/spatial_resistance_NL_cropped.tif")

# Set to True to calculate minimum, maximum, mean and standard deviation.
# This reads all raster cells and may take some time for large rasters.
CALCULATE_STATISTICS = False


def identify_epsg(crs):
    """Identify the EPSG code, including from ArcGIS-style WKT."""

    if crs is None:
        return None

    # Try Rasterio's EPSG matching using progressively lower confidence.
    for threshold in (70, 25, 0):
        try:
            epsg = crs.to_epsg(confidence_threshold=threshold)

            if epsg is not None:
                return int(epsg)

        except TypeError:
            # Compatibility with Rasterio versions that do not accept
            # confidence_threshold.
            epsg = crs.to_epsg()

            if epsg is not None:
                return int(epsg)

            break

    # Try identifying the CRS authority.
    try:
        authority = crs.to_authority(confidence_threshold=0)

        if authority is not None:
            authority_name, authority_code = authority

            if authority_name.upper() == "EPSG":
                return int(authority_code)

    except (AttributeError, TypeError, ValueError):
        pass

    # ArcGIS WKT1 fallback:
    # AUTHORITY["EPSG","3035"]
    wkt = crs.to_wkt()

    authority_matches = re.findall(
        r'AUTHORITY\["EPSG","(\d+)"\]',
        wkt,
        flags=re.IGNORECASE,
    )

    if authority_matches:
        # The final EPSG authority in WKT1 normally belongs to the complete
        # projected CRS. Earlier codes describe the ellipsoid, datum and
        # underlying geographic CRS.
        return int(authority_matches[-1])

    # WKT2 fallback:
    # ID["EPSG",3035]
    id_matches = re.findall(
        r'ID\["EPSG",\s*(\d+)\]',
        wkt,
        flags=re.IGNORECASE,
    )

    if id_matches:
        return int(id_matches[-1])

    return None


def get_compression(src):
    """Return the GeoTIFF compression method."""

    try:
        compression = src.compression

        if compression is not None:
            return getattr(compression, "value", str(compression))

    except AttributeError:
        pass

    # Fallback for Rasterio/GDAL versions that expose compression through tags.
    image_structure = src.tags(ns="IMAGE_STRUCTURE")

    return image_structure.get("COMPRESSION", "None/unknown")


def describe_geotiff(path, calculate_statistics=False):
    """Print dataset-level and band-level GeoTIFF metadata."""

    if not path.is_file():
        raise FileNotFoundError(f"GeoTIFF not found: {path}")

    with rasterio.open(path) as src:
        bounds = src.bounds
        cell_size_x, cell_size_y = src.res
        epsg = identify_epsg(src.crs)
        compression = get_compression(src)

        total_cells = src.width * src.height

        print("=" * 72)
        print("GEOTIFF METADATA")
        print("=" * 72)

        print(f"File:                   {path.resolve()}")
        print(f"Driver / format:        {src.driver}")
        print(f"Width (columns):        {src.width:,}")
        print(f"Height (rows):          {src.height:,}")
        print(f"Total grid cells:       {total_cells:,}")
        print(f"Number of bands:        {src.count}")
        print(f"Data type(s):           {', '.join(src.dtypes)}")

        print("\nCoordinate reference system")
        print("-" * 72)
        print(f"CRS:                    {src.crs or 'Not defined'}")
        print(
            f"EPSG code:              "
            f"{epsg if epsg is not None else 'Not available'}"
        )

        print("\nGrid properties")
        print("-" * 72)
        print(f"Cell size X:            {cell_size_x}")
        print(f"Cell size Y:            {cell_size_y}")
        print(f"Bounding box left:      {bounds.left}")
        print(f"Bounding box bottom:    {bounds.bottom}")
        print(f"Bounding box right:     {bounds.right}")
        print(f"Bounding box top:       {bounds.top}")
        print(f"NoData value(s):        {src.nodatavals}")
        print(f"Transform:\n{src.transform}")

        print("\nStorage properties")
        print("-" * 72)
        print(
            f"Color interpretation:   "
            f"{[item.name for item in src.colorinterp]}"
        )
        print(f"Compression:            {compression}")
        print(f"Tiled:                  {src.is_tiled}")
        print(f"Block shape(s):         {src.block_shapes}")

        # Overview/pyramid levels for each band.
        for band_number in range(1, src.count + 1):
            print(
                f"Overview levels band {band_number}: "
                f"{src.overviews(band_number)}"
            )

        # Explain how invalid cells are identified.
        print("\nMask information")
        print("-" * 72)

        for band_number, flags in enumerate(
            src.mask_flag_enums,
            start=1,
        ):
            flag_names = [flag.name for flag in flags]

            print(
                f"Band {band_number} mask flags: "
                f"{flag_names}"
            )

        dataset_tags = src.tags()

        if dataset_tags:
            print("\nDataset tags")
            print("-" * 72)

            for key, value in dataset_tags.items():
                print(f"{key}: {value}")

        # Print band-level information.
        for band_number in range(1, src.count + 1):
            print(f"\nBand {band_number}")
            print("-" * 72)

            band_index = band_number - 1

            print(f"Data type:              {src.dtypes[band_index]}")
            print(f"NoData:                 {src.nodatavals[band_index]}")
            print(
                f"Description:            "
                f"{src.descriptions[band_index] or 'None'}"
            )
            print(
                f"Unit:                   "
                f"{src.units[band_index] or 'None'}"
            )

            if calculate_statistics:
                # masked=True excludes cells identified as NoData.
                data = src.read(band_number, masked=True)

                valid_pixels = int(data.count())
                nodata_pixels = int(data.size - valid_pixels)

                print(f"Valid pixels:           {valid_pixels:,}")
                print(f"NoData pixels:          {nodata_pixels:,}")

                if data.size > 0:
                    nodata_percentage = (
                        nodata_pixels / data.size
                    ) * 100

                    print(
                        f"NoData percentage:      "
                        f"{nodata_percentage:.2f}%"
                    )

                if valid_pixels == 0:
                    print("Statistics:             no valid pixels")

                else:
                    print(f"Minimum:                {data.min()}")
                    print(f"Maximum:                {data.max()}")
                    print(f"Mean:                   {data.mean()}")
                    print(
                        f"Standard deviation:     "
                        f"{data.std()}"
                    )


def main():
    try:
        describe_geotiff(
            GEOTIFF_PATH,
            calculate_statistics=CALCULATE_STATISTICS,
        )

    except (
        FileNotFoundError,
        rasterio.errors.RasterioIOError,
    ) as exc:
        raise SystemExit(f"Error: {exc}") from exc


if __name__ == "__main__":
    main()