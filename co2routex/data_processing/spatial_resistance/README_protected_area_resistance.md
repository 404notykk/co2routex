# Protected-area resistance

`rasterise_protected_area_resistance.py` creates an **area-weighted protected-area multiplier between 1 and 30** for NL, DE and NO directly on the population-aligned 100 m grid. It prepares reusable country polygon layers before rasterisation. This version covers the terrestrial analysis area only; it does not construct a coastal buffer.

## Files and dependencies

Place the script beside the existing `prepare_reference_grid.py` in `co2routex/data_processing/spatial_resistance/`. The shared helper defines project paths, country boundaries, reference signatures and output profiles. Paths do not depend on PyCharm's working directory.

Install the packages in the Python interpreter selected for this PyCharm project:

```bash
python -m pip install numpy rasterio geopandas "shapely>=2" pyogrio pyproj
```

This script does not require the `osmium` or `ogr2ogr` command-line tools.

Inputs under `database/pipeline/`:

- Polygon shapefiles in `raw/protected_areas/WDPA_Jun2026_Public_shp_0/`, `_1/` and `_2/`. Each folder must contain one shapefile whose name includes `polygon`; retain its companion files. Set `SOURCE_FILES` in the script to use explicit polygon paths instead.
- Country boundaries returned by `prepare_reference_grid.boundary_path(code)`.
- `intermediate/reference_grid/COUNTRY_reference_grid.json`.
- `intermediate/reference_grid/COUNTRY_population_valid_mask_100m.tif`.
- The population source referenced by `prepare_reference_grid.py`, for signature validation.

Point records are excluded because they do not define a protected site's footprint. No point buffers or assumed site shapes are created. Raw source files are never changed.

## Area-weighted calculation

At each location, the highest applicable protected-area factor wins: **30** for IUCN category II, **10** for other protected polygons, and **1** for unprotected land. The cell receives the area-weighted mean of these local factors, rather than the highest factor found anywhere in the cell.

For each cell allowed by the population-valid mask, define:

- `A_domain`: area of the cell intersected with the supplied country boundary.
- `A_any`: area within that domain covered by the union of **all** protected polygons, including category II.
- `A_II`: area within that domain covered by the union of category-II polygons.

```text
multiplier = 1 + 9 × A_any / A_domain + 20 × A_II / A_domain
```

This is equivalent to `(unprotected_area × 1 + other_only_area × 10 + category_II_area × 30) / A_domain`. Unions prevent overlapping polygons from being counted twice; category II takes priority where categories overlap. The maximum is **30 for this layer**, while the final combined resistance can exceed 30 when multiplied by other layers.

| Coverage as a fraction of the cell's country domain | Multiplier |
| --- | ---: |
| 100% unprotected | 1 |
| 100% other protected category | 10 |
| 100% category II | 30 |
| 90% category II, 10% unprotected | 27.1 |
| 50% category II, 50% unprotected | 15.5 |
| 10% category II, 40% other-only, 50% unprotected | 7.5 |

Use **country-domain area**, not the full square, as the denominator at country boundaries. For example, if only 5,000 m² of a 10,000 m² cell lies inside the country boundary and 2,000 m² of that domain is category II, the result is `1 + 29 × 2,000 / 5,000 = 12.6`. Land outside the country cannot dilute its multiplier. Cells outside the analysis mask, or masked-in cells with zero country-domain area, are NoData (`-9999`); zero-domain cells are counted separately in the report.

Categories use the `IUCN_CAT` field: trimmed, case-insensitive `II` receives 30; other values, including missing or unspecified categories, receive 10. No designation-status filter is applied. Edge-only or point-only contact contributes zero area. There is no minimum coverage fraction; a small overlap contributes proportionally instead of assigning 30 or 10 to the entire cell.

This is the project's **area-weighted, capped protected-area policy**. It differs from Bogs' presence-based multiplication of separate protected-designation layers: a cell intersecting both classes receives a protection contribution of 300, even when those classes occupy separate parts of the cell. Their national-park classification uses designation `DE01`; this script's IUCN-II mapping is a different classification choice. References: [Bogs et al.](https://doi.org/10.1080/24725854.2025.2602823) and [released source code](https://zenodo.org/records/15829448), `network/graph_builder/builder.py`, function `apply_natural_protection_data`.

Multiplying this completed raster with other averaged layers is a cell-scale modelling choice. A product of layer averages is not generally the same as an area average of their pointwise product. This script implements only the protected-area layer.

## Processing workflow

1. **Inspect attributes without loading geometry.** Record source fields, category distributions and available country identifiers, including `ISO3` and `PARENT_ISO3` when present. The target identifiers are NLD, DEU and NOR; records containing multiple country codes are recognised.
2. **Select and clip country polygons.** Country attributes help identify records, but spatial checks preserve intersecting records with missing, ambiguous, different or incomplete identifiers. Bounding-box selection precedes exact clipping to the country boundary. A matching country attribute never substitutes for clipping a transboundary or partly marine polygon.
3. **Prepare one reusable country layer.** Reproject selected geometry to EPSG:3035, repair invalid geometry where needed, retain polygon area, assign 10/30, and combine source parts into an indexed GeoPackage layer. Separate multipart features into their polygon parts while retaining source identifiers; this avoids unnecessarily broad spatial-index bounds. Combining means collecting features into one layer; it does not dissolve the country into one polygon or discard category distinctions.
4. **Calculate areas directly at 100 m.** Query the polygon spatial index for each block and form block-local unions for all protected coverage and category-II coverage. Use rasterisation for interior cells and exact vector intersections for cells along polygon or country boundaries. Calculate the country-domain denominator and area-weighted multiplier, then apply the aligned population-valid mask.
5. **Write the output and report.** Record signatures, processing settings, continuous minimum/maximum/mean, coverage-area totals, mask and zero-domain counts, and stage timings. Reuse matching prepared vectors and raster outputs on later runs.

Country attributes establish selection priority, not a complete spatial filter. Attribute matches are restricted to the country bounding box, and the safety check also loads unmatched candidates in that box. This preserves transboundary or miscoded records but does not reduce the total set of bounding-box candidates read. The main savings are reusable prepared country layers and removal of mandatory 10 m processing.

The terrestrial extent is determined by the supplied country boundary together with the population-valid mask. The script does not add a new coastline dataset: offshore water included in those inputs is not independently removed. Land portions of mixed land/sea protected polygons can contribute inside the supplied extent. The valid mask should include valid zero-population terrain; it should not mean only cells with population greater than zero.

## Resolution and performance

Areas are calculated from vector geometry directly at 100 m, using Float64 calculations and Float32 raster output. There is no 10 m sampling approximation or intermediate raster. This **changes the previous maximum-category rule**; equivalence with the previous 10 m/MAX output is not expected. `--save-10m` remains unsupported, and existing 10 m files are not used.

Country polygons and their spatial index are held in memory. Raster arrays, unions and boundary intersections are processed block by block. Reducing block size reduces per-block work and memory, but does not eliminate the memory needed for the complete prepared country layer. Geometry reading, clipping and local unions can still dominate runtime; no fixed speedup is assumed.

## Outputs and cache reuse

All outputs are saved under `database/pipeline/intermediate/protected_areas/`:

- `source_audit/SOURCE_STEM_PATHKEY.json`: one cached attribute audit per source; the path key distinguishes identically named files in different folders.
- `COUNTRY_protected_area_polygons.gpkg`, layer `protected_areas`: prepared country geometry and classification, with a spatial index.
- `COUNTRY_protected_area_polygons.manifest.json`: country preparation inputs, settings and counts.
- `COUNTRY_protected_area_resistance_100m.tif`.
- `COUNTRY_protected_area_resistance.json`: raster report and reuse metadata.

The GeoTIFF is Float32, uses EPSG:3035 and NoData `-9999`, and inherits tiling/compression from the shared reference profile. Its transform, dimensions and extent match the 100 m reference grid. Valid values are continuous between 1 and 30, not restricted to three classes. The script checks reference signatures, mask alignment and mask-valid cell counts, and reports any masked-in cells excluded for zero country-domain area.

The JSON report provides `statistics` (minimum, maximum and arithmetic mean of the valid saved cell values), `area_totals_m2` (domain, any protection, category II, other-only and unprotected areas), `mask_valid_cells`, `valid_cells`, `zero_domain_cells` and `partial_domain_cells`. Area totals cover only masked-in cells. `valid_cells + zero_domain_cells` must equal `mask_valid_cells`; a large zero-domain count warrants checking the reference mask and boundary. Category-II area is already included in the any-protection total, so those two totals must not be added together.

An existing file alone is insufficient for reuse: its accompanying metadata must match current input signatures and settings. Source signatures track the paths, sizes and modification times of shapefile companion files; they are not full file-content hashes. Normal input changes invalidate the relevant cache, but edits preserving size and timestamps may go undetected. `--rebuild-vectors` forces source audits and country preparation; `--overwrite` forces raster generation. These options affect generated outputs, not raw inputs. Use one running instance per country/output directory so two processes do not overwrite the same outputs.

Raster processing version **3** invalidates previous maximum-category raster caches automatically. Compatible country polygon caches can still be reused. Outputs retain the same filenames, so a successful run replaces the previous raster and report. Valid unprotected background is **1**, not the original script's 0; zeros would force a multiplied combined resistance to zero.

## Run

In PyCharm, run the script with its default settings to process NL, DE and NO. For the terminal examples below, change to `co2routex/data_processing/spatial_resistance/`, using the same Python interpreter as PyCharm.

Inspect the source attributes first; reference grids and boundaries are not required for this mode:

```bash
python rasterise_protected_area_resistance.py --inspect-only
```

Start with the Netherlands, then process all three countries:

```bash
python rasterise_protected_area_resistance.py --countries NL
python rasterise_protected_area_resistance.py
```

Prepare vectors only, without requiring the population/reference rasters:

```bash
python rasterise_protected_area_resistance.py --countries NL --prepare-only
```

Force a rebuild or adjust the processing block size:

```bash
python rasterise_protected_area_resistance.py --countries NL --rebuild-vectors --overwrite
python rasterise_protected_area_resistance.py --countries DE NO --block-size 128
```

`--countries` accepts any selection of NL, DE and NO. `--block-size` is measured in 100 m cells per block edge; smaller blocks reduce per-block raster memory but may repeat more geometry work. Follow the printed stage timings to identify whether preparation or rasterisation dominates on your datasets.

## First-run checks

Development validation passed 96 deterministic/random geometry and domain cases against independent exact cell intersections, plus 30 block-partition comparisons. Real GeoTIFF checks covered continuous Float32 values, partial country cells, mask/NoData handling, report statistics and area totals, cache reuse, invalidation of old maximum-based rasters, and preservation of the previous raster when mask validation fails. The production reference helper was represented by compatible test fixtures; the actual NL/DE/NO datasets and full-country runtime were not tested here.

Inspect the audit and country preparation reports for unexpected country codes, absent categories, skipped geometry or unusual feature counts. Check that valid output values lie between 1 and 30, fully unprotected domain gives 1, and fully category-II domain gives 30. Inspect NoData and zero-domain counts against the supplied boundary and master mask.

On a representative small area, independently intersect polygons with cells and calculate the formula above. Include partial country cells, holes, small polygons, overlapping categories and exact edge contacts. Do not validate this new mean by demanding equality with the old maximum raster. Full-country runtime depends on the actual source data and server and must be measured locally.
