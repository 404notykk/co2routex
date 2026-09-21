# Population resistance

Classifies the JRC-ESTAT Census Population Grid 2021 on the existing 100 m
master grid into **multiplicative resistance factors**. A factor of 1 is
neutral. Multiply this factor with the other compatible resistance layers
in the integration stage. Population counts are neither resampled nor rounded.

## Reference

Bogs, S., Abdelshafy, A., and Walther, G. *Planning minimum regret CO2 pipeline
networks*. IISE Transactions.
[Publication DOI](https://doi.org/10.1080/24725854.2025.2602823).

The population density thresholds are verified against **Appendix B, Table 5
of the accessible arXiv v1 preprint**:
[Bogs et al., Table 5](https://arxiv.org/html/2502.12035v1#A2.T5).
This table uses **inhabitants per square kilometre**. Its table numbering is
specific to that preprint version.

The [official JRC dataset description](https://data.jrc.ec.europa.eu/dataset/98336641-fd1c-4992-8c7b-c470dd5eb81e)
defines the input values as **resident population counts per 100 x 100 m cell**.
Each such cell is 1 hectare, or 0.01 square kilometres:

```text
density in people/km² = people per 100 m cell * 100
threshold in people per 100 m cell = threshold in people/km² / 100
```

The script converts the original thresholds by dividing by 100, giving
`[2.5, 5, 20, 40, 80]`. It leaves the source raster values unchanged.
For example, 10 people in one cell means 1,000 people/km² and receives factor 9.

## Classification

| Original density, people/km² | Converted population per hectare / 100 m cell | Factor |
|---|---|---:|
| 0 <= d < 250 | 0 <= p < 2.5 | 1 |
| 250 <= d < 500 | 2.5 <= p < 5 | 4 |
| 500 <= d < 2,000 | 5 <= p < 20 | 9 |
| 2,000 <= d < 4,000 | 20 <= p < 40 | 16 |
| 4,000 <= d < 8,000 | 40 <= p < 80 | 25 |
| d >= 8,000 | p >= 80 | 36 |

Exact thresholds enter the higher class: for example, 5 receives factor 9
and 80 receives factor 36. This matches the upper-bound comparisons in the
[authors' released classifier](https://zenodo.org/records/15829448),
`network/graph_builder/graph_params.py`, with thresholds configured in
`network/designer/data/opt_data/params/params.json`.

A valid count of zero receives resistance 1. Missing population and cells
excluded by the population-valid mask remain NoData (-9999). Nonfinite or
negative values in included cells cause a clear error rather than being
classified. The mask must agree with the source's NoData definition.

An included border or coast cell retains its original count per full 100 m
cell. No population redistribution or division by its in-country area is
performed. This step uses the population raster directly at 100 m.

## Inputs

Paths are relative to `co2routex/database/pipeline/`.

| Input | Path |
|---|---|
| Population | `raw/population/JRC-ESTAT_Census_Population_2021_100m.tif` |
| Grid definition | `intermediate/reference_grid/COUNTRY_reference_grid.json` |
| Valid-data mask | `intermediate/reference_grid/COUNTRY_population_valid_mask_100m.tif` |
| Boundary signature check | `intermediate/boundaries/COUNTRY.shp` and companion files |

COUNTRY defaults to NL, DE and NO. Population and boundary signatures are
checked when loading each grid. Population-window alignment, dimensions,
CRS, mask values and the valid-cell count are also checked during processing.

## Outputs

Under `intermediate/population/`:

- `COUNTRY_population_resistance_100m.tif`: Float32 resistance, EPSG:3035,
  exact 100 m reference alignment, NoData=-9999, tiled lossless compression.
- `COUNTRY_population_resistance.json`: classification thresholds, endpoint
  rule, publication reference, per-class cell counts, included population
  statistics, multiplier statistics, processing times and source/grid records.

The population-valid mask defines the included cells. The included-population
sum is a diagnostic of those cells, not an independently validated national
census total. The DOI is also recorded in the GeoTIFF metadata.

The JSON report includes:

| Field | Meaning |
|---|---|
| `thresholds_people_per_km2` | Original density thresholds from the paper |
| `thresholds_people_per_hectare` | Converted thresholds used with the input cell counts |
| `class_cell_counts` | Number of valid cells in each multiplier class |
| `class_cell_percentages` | Percentage of valid cells in each class; NoData excluded |
| `statistics.min`, `statistics.max` | Lowest and highest multiplier actually present |
| `statistics.mean` | Arithmetic mean multiplier across valid cells |
| `processing_blocks` | Total blocks, blocks that read population, and entirely excluded blocks skipped |
| `timings_seconds` | Input opening/validation, mask reads, population reads, classification/statistics, writes and total raster-stage time |

Class percentages sum to approximately 100%, allowing floating-point
rounding. Mean multiplier is calculated as
`sum(multiplier * class_cell_count) / valid_cells`; it is not weighted by
population. It summarises the raster and is not a routed pipeline's
length-weighted resistance.

Times are measured in seconds per country. Total raster-stage time ends
after closing the temporary TIFF, before JSON writing and final file
replacements. Component timings need not sum to the total because the total
also includes output setup, closing and other overhead.

## Settings and usage

| Setting | Default | Meaning |
|---|---|---|
| COUNTRIES | NL, DE, NO | Countries to classify |
| BLOCK_SIZE | 256 | Process up to 256 x 256 cells per block; each cell remains 100 m |
| NODATA | -9999 | Excluded output cells |

With the listed inputs available, run from the script's folder:

```bash
python rasterise_population_resistance.py
python rasterise_population_resistance.py --countries NL
```

The first command processes all three countries; the second selects NL only.
Rerunning replaces the corresponding generated outputs. The source population
and mask files are not modified. Keep `prepare_reference_grid.py` beside this
script because it supplies the shared paths and helper functions.

The script reads the inclusion mask first for each block. If every mask cell
is excluded, it writes an all-NoData output block without reading population
values for that block. Mixed blocks are read and classified normally. This
reduces unnecessary data reads without changing the output values; the
runtime benefit depends on how many blocks are entirely excluded and on I/O.

Dependencies are the same as the reference-grid script: NumPy, Rasterio,
GeoPandas, Shapely and PyProj. No 10 m grid is used by this population step.

## Validation

This revision passed 10 synthetic tests using Python 3.12, Rasterio 1.5.1,
GeoPandas 1.1.4 and Shapely 2.1.2. Checks covered the unit conversion,
thresholds, fractional and zero counts, invalid values, source-window
offsets, mask handling, percentages, mean multiplier and timing fields.
A reader spy confirmed that wholly excluded blocks perform no population
read. Block sizes 1, 2, 3 and 256 produced identical raster values. An invalid
mask left both previous output TIFF and JSON unchanged.

Full-country population rasters have not been validated as part of this revision.
