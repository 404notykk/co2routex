# Integrated spatial resistance

`integrate_resistance_layers.py` multiplies seven completed 100 m resistance
factors for NL, DE and NO. A factor of **1 is neutral**. Pipeline factors below
one reduce the product. All seven factors must be valid for an output cell;
missing values remain NoData.

Keep this script alongside `prepare_reference_grid.py` in
`co2routex/data_processing/spatial_resistance/`. Dependencies are NumPy,
Rasterio and Affine, plus the GeoPandas, Shapely and PyProj dependencies of the
reference helper.

## Inputs and compatible values

Paths below are relative to `database/pipeline/`. `COUNTRY` is NL, DE or NO.

| Layer | Valid factors | Raster |
| --- | --- | --- |
| Motorways | 1-3 | `intermediate/existing_motorways/COUNTRY_motorways_resistance_100m.tif` |
| Pipelines | 0.25-1 | `intermediate/existing_pipelines/COUNTRY_pipelines_resistance_100m.tif` |
| Railways | 1-3 | `intermediate/existing_railways/COUNTRY_railways_resistance_100m.tif` |
| Water | 1-10 | `intermediate/water/COUNTRY_water_resistance_100m.tif` |
| Slope | 1-20 | `intermediate/slope/COUNTRY_slope_resistance_100m.tif` |
| Protected areas | 1-30 | `intermediate/protected_areas/COUNTRY_protected_area_resistance_100m.tif` |
| Population | Exactly 1, 4, 9, 16, 25 or 36 | `intermediate/population/COUNTRY_population_resistance_100m.tif` |

All seven files must exist and be distinct. Each must contain one numeric band
in EPSG:3035 with north-up 100 m cells, the same origin and dimensions as the
reference grid, and default scale/offset metadata. Source alignment errors stop
processing; no reprojection or resampling occurs.

The script also reads `intermediate/reference_grid/COUNTRY_reference_grid.json`
and its population-valid mask. Population and boundary source files remain
necessary for the reference helper's signature checks.


## Calculation

```text
R = R_motorways * R_pipelines * R_railways * R_water
    * R_slope * R_protected_areas * R_population
```

Each supplied factor is applied once. No additional baseline, weighting,
exponent, floor at one, or global cap is applied.

| Example within the valid mask | Integrated multiplier |
| --- | ---: |
| All seven factors equal 1 | 1 |
| Pipeline factor 0.25; all other factors 1 | 0.25 |
| Population 4, slope 2, pipeline 0.625; other factors 1 | 5 |
| Protected areas 30 and population 4; other factors 1 | 120 |

The protected-area cap of 30 applies only to that individual layer. The
integrated multiplier can exceed 30 after multiplying other factors. The
configured ranges imply a theoretical product range of 0.25-1,944,000; this is
not an expected observed country range or an extra clipping rule.

### Order of averaging and multiplication

CO2RouteX multiplies the **already aggregated 100 m factors**. It does not
multiply finer-resolution values first and average afterward. These operations
are generally different because the completed rasters do not preserve
within-cell overlap between features.

For example, if a pipeline and railway occupy the same 10% of a fully included
cell, their independently averaged factors are 0.925 and 1.2. This script
multiplies them to obtain **1.11**. Multiplying at the fine-pixel level first
would give `0.9 * 1 + 0.1 * 0.25 * 3 = 0.975`, a different modelling convention.
No additional occupancy weighting is applied during integration.

The degree-to-multiplier mapping, protected-area overlap rule, network occupancy
method and water classification remain the responsibility of their producers.

## Validity and missing data

| Cell condition | Output |
| --- | --- |
| Included by the population mask; all seven factors valid | Product of all seven factors |
| Included; any required layer is NoData | NoData |
| Included; all factors are missing | NoData |
| Outside the population-valid mask | NoData |
| Included; an available factor is zero, negative, nonfinite or incompatible | Error; current country's completed outputs are not replaced |

A known absence of a feature must already be represented by **1** in its layer.
Unknown data is not the same as a known absence, so NoData is never silently
replaced with one or omitted from the product. This can reduce the output domain
if a layer has incomplete coverage. The report quantifies those lost cells.
An entire country without any cell having all seven factors raises an error.

The script respects each input raster's mask and declared NoData. It validates
available factors even where another factor is missing. Values outside the
population mask do not participate. Entirely excluded processing blocks skip
all seven input reads and are written as NoData.

## Producer reports and provenance

When a neighbouring producer report exists, the script checks it before writing
outputs. Reports use the same basename with `_100m.tif` replaced by `.json`.
Checks cover country, output name, reference dimensions/transform, available
source signatures, neutral-background declarations and mask signatures. Network,
protected-area, population, slope and water report formats are supported.

Mask signatures are read from `mask_signature`, then `cache_key.inputs.mask`
(network reports), then `cache_key.mask` (protected-area reports). The network
report's separate `cache_key.mask` text describes the masking rule; it is not a
file signature. Actual supplied signatures remain subject to metadata checks.

Where a producer supplies an output signature, it must match the TIFF. Network
reports use `output_signature`; protected-area reports use `raster_signature`.
Other reports may omit that signature. File metadata signatures are compared
using filename, size and modification time, allowing project-root paths to
differ. A timestamp truncated to whole seconds during transfer is accepted only
when both timestamps fall in the same integer second and at least one has no
fractional second. Two different fractional timestamps, different whole seconds,
different filenames or different sizes still fail. This rule applies to
reference sources, embedded reference-source signatures, and producer mask and
output signatures. Original signatures and file timestamps are retained; the
report records the comparison policy. These are not cryptographic checksums and cannot prove byte identity or
detect every same-size edit within a second. Precision loss alone does not
require regenerating a grid or its rasters.

An absent producer report is recorded as `not_available`; raster metadata and
cell values are still checked. A present but inconsistent report is an error.
The integrated report explicitly records which checks were possible. Matching
metadata cannot independently certify source-data quality or reconstruct an
earlier producer run, especially when no output signature is available.

## Outputs and statistics

- `spatial_cost_resistance/spatial_resistance_COUNTRY.tif`
- `spatial_cost_resistance/spatial_resistance_COUNTRY.json`

Products accumulate in Float64 and are saved as Float32. The output uses the
reference transform, dimensions and CRS; NoData is `-9999`, compression is
DEFLATE, and storage tiles are 256 x 256 cells. The TIFF is tagged `PRODUCT` and
method version 2, distinguishing it from the earlier additive output.

The JSON records:

- Integration formula, neutral factor, method version and strict missing rule.
- Input file signatures and producer-report validation results.
- Per-layer valid/missing counts, missing percentages, minima, maxima and means.
- Per-layer and final counts below 1, exactly 1 and above 1, named
  `discounted_cells`, `neutral_cells` and `penalised_cells`.
- Complete, partial and entirely missing input coverage, plus a histogram of
  available layer counts from zero to seven.
- Cells and percentage dropped because at least one factor was missing, and
  total output NoData cells including those outside the population mask.
- Processing time and counts of blocks read or skipped.

Per-layer statistics use that layer's valid values inside the population mask.
Final statistics use only the saved Float32 products where all seven factors
are available. The final mean is computed from those products, not by
multiplying country-wide layer means. Input coverage statistics describe raster
validity; they cannot detect missing observations already concealed by a producer.

`processing_blocks.layers_read` counts blocks in which the seven inputs were
read, not the number of individual raster read calls. Timing covers each
country's processing and its preflight through closing the temporary TIFF; it
excludes the main command's initial preflight, JSON writing and final file
replacements. Block-write timings exclude deferred closing work, which remains
part of the total raster-stage time.

## Execution

First finish all seven layer producers, including the updated water producer.
From `co2routex/data_processing/spatial_resistance/`:

```bash
python rasterise_water_resistance.py
python integrate_resistance_layers.py --check-only
python integrate_resistance_layers.py
```

To process one country:

```bash
python integrate_resistance_layers.py --countries NL
```

`--check-only` checks file availability, grid metadata, declared semantics and
available producer reports. It does **not** scan raster values or write outputs;
factor ranges and actual coverage are checked during the full run.

Default countries are NL, DE and NO. `BLOCK_SIZE = 256` controls processing
memory and I/O grouping, not output resolution. Every selected country's input
metadata is checked before processing begins. Outputs are staged per country
and replace their named files only after that country's processing succeeds.
TIFF and JSON replacements are sequential; countries already completed are not
rolled back if a later country fails. No input datasets are modified.

Methodological reference: [Bogs et al., *Planning minimum regret CO2 pipeline
networks*](https://doi.org/10.1080/24725854.2025.2602823) and the
[released implementation](https://zenodo.org/records/15829448). CO2RouteX's
100 m averaging and slope mapping are documented modelling adaptations.
