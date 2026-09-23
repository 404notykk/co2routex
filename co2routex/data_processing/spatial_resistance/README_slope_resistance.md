# Slope resistance

`rasterise_slope_resistance.py` processes NL, DE and NO on the population-aligned
100 m grid. It imports the shared helpers in `prepare_reference_grid.py`, which
must be available alongside it.

Dependencies: NumPy, Rasterio and Affine, plus the GeoPandas, Shapely and PyProj
dependencies imported by `prepare_reference_grid.py`.

## Inputs

Paths are relative to `database/pipeline/`:

- `raw/slope/eudem_slop_3035_europe.tif`
- `intermediate/reference_grid/COUNTRY_reference_grid.json`
- `intermediate/reference_grid/COUNTRY_population_valid_mask_100m.tif`

Population and boundary source files must also remain available for the shared
input signature checks. File signatures record names, sizes and modification
times; they are not cryptographic checksums.

## Method and units

1. Read the valid 25 m source values, using the raster's NoData and mask metadata.
2. Decode each source value into an angle:
   `degrees = acos(DN / 250.0) * 180 / pi`.
3. Average the decoded angles over each aligned group of 4 x 4 source cells.
4. Calculate `resistance = 1 + 19 * mean_degrees / 90`.
5. Write results only where the population-valid mask is one and the selected
   slope missing-data policy is satisfied.

The [official EU-DEM conversion sheet](https://ec.europa.eu/eurostat/documents/7116161/7172326/SPEC011-b140109-SLOP.pdf)
specifies the decoding formula. DN is an encoded value, not an angle or a
percentage gradient. Examples: DN 250 is 0 degrees, DN 200 is approximately
36.9 degrees, and DN 125 is 60 degrees. Unmasked DN zero represents 90 degrees.
If the source declares zero as NoData or masks it, those zeros remain missing.

Decode before averaging: applying `acos` to a mean DN gives a different answer.
The sixteen 25 m cells have equal area, so their arithmetic mean is an
area-weighted mean over the 100 m cell. With the linear resistance formula,
averaging decoded angles and then calculating resistance is also mathematically
equivalent to averaging their individual resistance multipliers.

Each included 100 m cell uses its full 4 x 4 source footprint. Its source cells
are not additionally clipped to the country boundary; country inclusion follows
the existing population-valid mask. This is the mean of local slope angles,
not a slope newly calculated from a 100 m elevation raster or a slope measured
along a particular pipeline route.

### Relationship to Bogs et al.

[Bogs et al., arXiv v1, Appendix B, Table 5](https://arxiv.org/html/2502.12035v1#A2.T5)
lists terrain slope 0-90 degrees and multipliers 1-20. CO2RouteX interprets these
endpoints using a linear relationship in degrees:

| Mean slope | Multiplier |
| --- | ---: |
| 0 degrees | 1 |
| 30 degrees | 7.3333 |
| 60 degrees | 13.6667 |
| 90 degrees | 20 |

The table does not explicitly prescribe linear interpolation. This relationship
is a CO2RouteX modelling assumption, not a claim of exact code reproduction or
an independently calibrated construction-cost function.

The [authors' released implementation](https://zenodo.org/records/15829448)
uses `1 + terrain_factor * (250 - DN) / 250`, with `terrain_factor = 20` in the
released parameters, on its selected DN statistic. It does not average decoded
angles as this script does. For DN in 0-250, that formula theoretically spans
1-21 and is nonlinear in degrees. CO2RouteX retains the explicit degree-based
mean and the paper's stated 1-20 endpoints.

Slope resistance is dimensionless and continuous. A value of one is neutral;
the slope layer is multiplied with the other resistance layers downstream.
NoData remains missing and is not automatically replaced by one.

Publication: Bogs, S.; Abdelshafy, A.; Walther, G., *Planning minimum regret CO2
pipeline networks*, [DOI](https://doi.org/10.1080/24725854.2025.2602823).

## Alignment and missing data

The script requires north-up 25 m source cells nested exactly within the 100 m
grid, with both rasters in EPSG:3035. It stops on incompatible alignment,
non-default source scale/offset metadata, or unmasked, relevant DN values outside
0-250 or nonfinite values. It does not shift the source, resample it, or clip
unexpected DN values into range.

| Policy | Valid source cells required per included 100 m cell | Result |
| --- | --- | --- |
| `--missing propagate` (default) | All 16 | Any missing source cell makes the output NoData |
| `--missing ignore` | At least 1 | Average the available source angles; zero valid source cells remains NoData |

Both policies give identical results when all sixteen source cells are valid.
Source cells outside the raster extent are missing. With `ignore`, even one
valid 25 m cell can represent the full output cell, so inspect the coverage
report when using that option. If a country has no usable output slope cells,
the script raises an error and does not replace that country's existing outputs.

## Outputs and report

Files are saved under `intermediate/slope/`:

- `COUNTRY_slope_degrees_100m.tif`
- `COUNTRY_slope_resistance_100m.tif`
- `COUNTRY_slope_resistance.json`

Both TIFFs use Float32, EPSG:3035, the reference transform and dimensions,
Deflate compression, 256 x 256 storage tiles, and NoData `-9999`. The possible
resistance range is 1-20; a country's observed range may be narrower.

The JSON retains the existing report fields and adds:

| Field | Meaning |
| --- | --- |
| `method_version`, `method`, `report_schema_version` | Explicit method and report identifiers; method version 1 retains the existing calculation |
| `conversion_source`, `publication` | Decoding reference, multiplier endpoints and the CO2RouteX interpolation assumption |
| `source_metadata` | Source CRS, pixel resolution, data type, dimensions, transform, scale/offset and mask flags |
| `mean_degrees` | Mean of valid saved 100 m slope angles |
| `multiplier_statistics` | Minimum, maximum and mean of valid saved multipliers |
| `nodata_cells` | All output NoData cells, including cells outside the population mask |
| `missing_slope_percentage_within_population_mask` | Percentage of population-valid cells with no output slope under the selected policy |
| `source_coverage` | Counts and percentages with complete (16), partial (1-15), or absent (0) valid source-cell coverage |
| `processing_blocks` | Total blocks, blocks with source reads, and entirely excluded blocks skipped |
| `timings_seconds` | Input loading, mask reading, source reading, aggregation/statistics, block writing and total raster-stage time |

Degree and multiplier statistics exclude NoData. Means accumulate the Float32
values written to the TIFFs using Float64 sums. Source-coverage percentages use
all population-valid 100 m cells as their denominator and do not change with the
missing-data policy. Under `propagate`, partial plus absent coverage becomes
missing output; under `ignore`, only absent coverage becomes missing output.

Timing covers each country's raster stage through closing both temporary TIFFs.
It excludes the main command's initial reference checks, JSON writing and final
file replacements. Block-write time measures write calls; final flushing,
compression on close, setup and other overhead are included in total time.
Consequently, component times need not sum to the total and these timings are
not the full command's wall-clock duration.

## Execution

Place both Python scripts in `co2routex/data_processing/spatial_resistance/`.
From that directory:

```bash
python rasterise_slope_resistance.py
python rasterise_slope_resistance.py --countries NL
python rasterise_slope_resistance.py --countries NO --missing ignore
```

Default countries are NL, DE and NO. `BLOCK_SIZE = 256` is the number of 100 m
cells along a processing block edge; changing it changes processing memory and
I/O grouping, not the aggregation method. `FACTOR = 4` describes the 25 m to
100 m aggregation. No 10 m intermediate grid is used for slope.

Entirely excluded blocks already skip source reads while writing NoData to both
outputs. Every run processes the selected countries again. Each country's
completed TIFFs and JSON replace their existing names sequentially after
processing succeeds; no manual deletion is needed before rerunning. Source
datasets are not modified.

## Source credit

European Commission - DG ENTR, 2012, EU-DEM Version 1.

"Data funded under GMES preparatory action 2009 on Reference Data Access by the
European Commission, DG Enterprise and Industry."
