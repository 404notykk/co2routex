# Water resistance

`rasterise_water_resistance.py` creates water multipliers for NL, DE and NO on the population-aligned 100 m grid. It uses shared helpers from `prepare_reference_grid.py`. Method version 2 uses **1 for classified non-water and 10 for permanent water**, allowing subsequent multiplication with the other resistance layers.

## Inputs

Default inputs are in `database/pipeline/raw/water/`. Paths below are relative
to `database/pipeline/`:

| Country | Land-cover raster files |
| --- | --- |
| NL | `raw/water/LCM_10_NL.tif` |
| DE | `raw/water/LCM_10_DE.tif` |
| NO | `raw/water/LCM_10_2020_NO_A.tif` and `raw/water/LCM_10_2020_NO_B.tif` |

`INPUT_FILES` specifies the filenames. Files must retain the original LCFM MAP class values. Already reclassified resistance rasters are not suitable inputs.

Alternatively, `--raw-tiles` reads `*_MAP.tif` files recursively under `raw/water/`. Quality layers are excluded by this filename pattern. Tiles outside the country's reference extent are skipped. No full-country mosaic is written.

The script also reads:

- `intermediate/reference_grid/COUNTRY_reference_grid.json`
- `intermediate/reference_grid/COUNTRY_population_valid_mask_100m.tif`

Population and boundary source files remain necessary for reference-signature checks. All readers must be single-band georeferenced MAP rasters with unchanged scale and offset.

Source metadata checks require matching filenames and byte sizes. Modification
timestamps must match exactly, except that a timestamp truncated to whole
seconds during transfer is accepted when both values fall in the same integer
second and at least one has no fractional second. Two different fractional
timestamps or different whole seconds still fail. Original JSON signatures and
file timestamps are retained; the report records this comparison policy.
These are metadata checks, not content checksums: matching metadata cannot prove
byte identity or detect every same-size edit within a second. A precision-only
mismatch does not require regenerating a grid or its rasters.

## Calculation

1. Sample land-cover values onto the nested 10 m EPSG:3035 grid using nearest-neighbour reprojection.
2. Combine source coverage. The first valid observation is retained; missing observations can be filled by later files. Conflicting water/non-water observations in overlapping files stop processing. Different non-water class labels do not conflict because both give multiplier 1.
3. Assign **10 to class 100 (permanent water bodies)** and **1 to other classified cells**. Wetlands and other land-cover categories do not count as class 100 water.
4. Keep source-masked observations, class 254 (unclassifiable) and class 255 (NoData) missing. They are never assumed to be non-water.
5. Average the classified multipliers in each 10 × 10 group onto the 100 m grid, using the missing-data policy below. Exclude cells outside the population-valid mask.

For a cell with complete source coverage:

```text
water_fraction = number of water subcells / 100
water_multiplier = 1 + 9 × water_fraction
```

| Water coverage | Mean multiplier |
| --- | ---: |
| 0% | 1 |
| 10% | 1.9 |
| 50% | 5.5 |
| 100% | 10 |

This is a coverage estimate on the sampled 10 m grid. The full footprint of each included 100 m cell contributes: the population-valid mask is repeated over its 100 fine cells, without additional polygon clipping at country boundaries.

### Missing data

- **Default `--missing propagate`:** all 100 fine cells must have a classified observation. Any remaining missing or unclassifiable fine cell makes the 100 m result NoData.
- **Optional `--missing ignore`:** use `water_fraction = water subcells / observed subcells`. One valid fine cell is sufficient. Missing area is excluded from the mean; no observations still means NoData. This preserves more output cells but estimates their multiplier from only the observed part.

An entirely unusable country raises an error. Neither policy assigns multiplier 1 simply because source coverage is absent. Product codes and masks identify missing coverage; the report cannot independently establish that every unmasked source measurement is valid.

## Outputs and reporting

All generated files are stored under `database/pipeline/intermediate/water/`:

- `COUNTRY_water_resistance_100m.tif`
- `COUNTRY_water_resistance.json`
- `COUNTRY_water_resistance_10m.tif` only with `--save-10m`.

GeoTIFFs use EPSG:3035, Float32, DEFLATE compression and NoData `-9999`. Valid values in the 100 m output lie between 1 and 10. The 100 m dimensions and transform match the reference grid.

The optional 10 m raster contains observed water 10, observed non-water 1 and missing/excluded cells -9999. Observed fine cells remain visible even when their parent 100 m cell receives NoData under `propagate`. This raster is diagnostic; integration uses only the 100 m output. Saving it is off by default. An older optional 10 m file is not refreshed or deleted when this option is off; the current report identifies whether a fine raster was generated.

The JSON records:

- Method version, formula, neutral/background multiplier, missing-data policy, grid and mask signature.
- Source paths, file signatures, CRS, dimensions, transform, resolution in source CRS units, data type, NoData and mask flags.
- Observed source class counts and numbers of water, missing and unclassifiable 10 m cells. These cover all population-valid cell footprints, including cells subsequently excluded by `propagate`.
- Complete (100 observations), partial (1–99) and absent (zero) coverage counts and percentages, relative to all population-valid 100 m cells.
- Output valid/NoData counts and the number and percentage of population-valid cells lost under the chosen policy.
- Minimum, maximum and mean of the written Float32 multipliers, excluding NoData; water-affected output cells have multiplier greater than 1.
- Total processing blocks, blocks with source reads, entirely excluded blocks skipped, and nonempty blocks outside all source bounds.
- Per-country timing for input setup, mask reads, source reads/mosaics, aggregation/statistics, output writes and the total raster stage. Total timing includes closing the temporary TIFFs but excludes JSON writing and final file replacements.

Entirely excluded blocks skip land-cover reads and receive NoData. Raw and intermediate input rasters remain unchanged.

## Replacing legacy outputs

**Rerun this script before multiplicative integration if the existing water raster used the former 0–10 convention.** Changing only zero cells to 1 is incorrect: a fully observed cell with 50% water previously had value 5, and must now have value 5.5.

For fully observed cells the algebraic conversion would be `1 + 0.9 × old_value`, but old outputs treated missing coverage as zero as well. They do not retain enough per-cell information to reconstruct the new missing-data policy. Regenerating from the original classified sources preserves the distinction between observed non-water and missing data.

## Execution

Dependencies: NumPy, Rasterio, GeoPandas, Shapely and PyProj.

```bash
python rasterise_water_resistance.py
python rasterise_water_resistance.py --countries NL
python rasterise_water_resistance.py --countries NO --save-10m
python rasterise_water_resistance.py --countries NL --raw-tiles
python rasterise_water_resistance.py --countries NO --missing ignore
```

Default countries: NL, DE and NO. `BLOCK_SIZE = 128` controls memory use and does not change resolution. Each country's outputs are staged until processing and validation complete, then replace its named outputs individually. A validation failure before replacement leaves previous outputs intact; the set of TIFF/JSON replacements is not a single atomic transaction.

## Data source

Copernicus Land Monitoring Service, **Land Cover 2020 (raster 10 m), global, annual — version 1**.

- [Dataset and citation](https://doi.org/10.2909/602507b2-96c7-47bb-b79d-7ba25e97d0a9)
- [LCFM class catalogue](https://stacbrowser.terrascope.be/collections/lcfm-lcm-10?.language=en)
