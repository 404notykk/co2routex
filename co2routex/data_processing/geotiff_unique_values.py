#!/usr/bin/env python3
"""Show the first 50 sorted unique values in a GeoTIFF.

Edit RASTER_PATH below and run this file directly in PyCharm.
The raster is processed block-by-block, so the full dataset is not loaded
into memory at once.
"""

from pathlib import Path

import numpy as np

try:
    import rasterio
except ImportError as exc:
    raise SystemExit(
        "Rasterio is required. Install it with: pip install rasterio"
    ) from exc


# -----------------------------------------------------------------------------
# USER SETTINGS
# -----------------------------------------------------------------------------
RASTER_PATH = Path("../database/pipeline/spatial_cost_resistance/spatial_resistance_NL.tif")
BAND_NUMBER = 1
NUMBER_TO_SHOW = 50
INTEGER_TOLERANCE = 1e-6


def inspect_unique_values(path: Path, band_number: int = 1) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"GeoTIFF not found: {path}")

    smallest_unique = np.array([], dtype=np.float64)
    valid_cell_count = 0
    fractional_cell_count = 0
    has_more_unique_values = False
    fractional_examples: list[float] = []

    with rasterio.open(path) as src:
        if not 1 <= band_number <= src.count:
            raise ValueError(
                f"Band {band_number} does not exist; raster has {src.count} band(s)."
            )

        print(f"File: {path.resolve()}")
        print(f"Band: {band_number}")
        print(f"Stored data type: {src.dtypes[band_number - 1]}")
        print(f"NoData value: {src.nodatavals[band_number - 1]}")

        for _, window in src.block_windows(band_number):
            # masked=True excludes cells identified by the raster's NoData mask.
            values = src.read(band_number, window=window, masked=True).compressed()
            if values.size == 0:
                continue

            values = values.astype(np.float64)
            values = values[np.isfinite(values)]
            if values.size == 0:
                continue

            valid_cell_count += values.size

            fractional_mask = ~np.isclose(
                values,
                np.rint(values),
                rtol=0.0,
                atol=INTEGER_TOLERANCE,
            )
            fractional_cell_count += int(fractional_mask.sum())

            if fractional_mask.any() and len(fractional_examples) < 10:
                remaining = 10 - len(fractional_examples)
                fractional_examples.extend(
                    values[fractional_mask][:remaining].tolist()
                )

            block_unique = np.unique(values)
            combined = np.union1d(smallest_unique, block_unique)

            if combined.size > NUMBER_TO_SHOW:
                has_more_unique_values = True
                smallest_unique = combined[:NUMBER_TO_SHOW]
            else:
                smallest_unique = combined

    print(f"Valid cells examined: {valid_cell_count:,}")
    print(f"\nFirst {min(NUMBER_TO_SHOW, smallest_unique.size)} unique values:")

    for index, value in enumerate(smallest_unique, start=1):
        print(f"{index:>3}: {value:.15g}")

    if has_more_unique_values:
        print(f"\nThe raster contains more than {NUMBER_TO_SHOW} unique values.")
    else:
        print(f"\nTotal unique values: {smallest_unique.size}")

    print(f"Cells with meaningful decimals: {fractional_cell_count:,}")

    if fractional_cell_count == 0:
        print(
            "Conclusion: all valid cell values are effectively integers "
            f"within a tolerance of {INTEGER_TOLERANCE}."
        )
    else:
        print("Conclusion: the raster contains genuine decimal values.")
        print("Example decimal values:")
        for value in fractional_examples:
            print(f"  {value:.15g}")


def main() -> None:
    try:
        inspect_unique_values(RASTER_PATH, BAND_NUMBER)
    except (FileNotFoundError, ValueError, rasterio.errors.RasterioIOError) as exc:
        raise SystemExit(f"Error: {exc}") from exc


if __name__ == "__main__":
    main()
