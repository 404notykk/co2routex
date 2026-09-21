# CO2RouteX: network resistance on the 100 m reference grid

`rasterise_network_resistance.py` converts existing motorways, pipelines and
railways into separate **multiplicative resistance factors**. It uses the
existing 100 m EPSG:3035 reference grid, measures network occupancy on its
nested 10 m grid, and averages each network type independently.

**A valid cell without that network has factor 1.** NoData is −9999.


## Files, dependencies and run order

Keep `rasterise_network_resistance.py` beside the existing
`prepare_reference_grid.py` in `co2routex/data_processing/spatial_resistance/`.
The network script imports the reference module for project paths, boundary
loading, fingerprints, raster profiles and window iteration. It expects
reference-grid JSON schema version 1, with a 100 m grid and factor-10 nested
10 m grid in EPSG:3035.

Use Python 3.10 or later. Install in the interpreter selected in PyCharm:

```bash
python -m pip install numpy rasterio geopandas 'shapely>=2' pyproj pyogrio
```

Paths below are relative to `co2routex/database/pipeline/`:

| Input | Expected location |
|---|---|
| Population | `raw/population/JRC-ESTAT_Census_Population_2021_100m.tif` |
| Country boundary | `intermediate/boundaries/COUNTRY.shp` and companion files |
| Reference geometry | `intermediate/reference_grid/COUNTRY_reference_grid.json` |
| Master inclusion mask | `intermediate/reference_grid/COUNTRY_population_valid_mask_100m.tif` |
| Motorways | `intermediate/existing_motorways/COUNTRY_motorways.gpkg` |
| Pipelines | `intermediate/existing_pipelines/COUNTRY_pipelines.gpkg` |
| Railways | `intermediate/existing_railways/COUNTRY_railways.gpkg` |

`COUNTRY` is `NL`, `DE` or `NO`. All network GeoPackages must contain a
`lines` layer, as produced by `osm_subset.py`. The layer must have a CRS and
valid, nonempty LineString or MultiLineString geometries; an empty layer is
allowed. Networks are reprojected to EPSG:3035 in memory. Input GIS files are
not edited. Missing files or invalid geometry are errors, not empty networks.

Run grid preparation first, then network rasterisation. From the scripts'
folder, a small initial run is:

```bash
python prepare_reference_grid.py --countries NL
python rasterise_network_resistance.py --countries NL --features railways
```

Run all three countries and all three network types:

```bash
python rasterise_network_resistance.py
```

Optional settings:

```bash
python rasterise_network_resistance.py --countries DE NO --features motorways pipelines
python rasterise_network_resistance.py --countries NL --save-10m
python rasterise_network_resistance.py --countries NL --block-size 64 --overwrite
```

| Option | Default / purpose |
|---|---|
| `--countries` | `NL DE NO`; select one or more countries |
| `--features` | `motorways pipelines railways`; select network types |
| `--save-10m` | Off; retain optional fine-resolution factor TIFFs |
| `--block-size` | 128; positive number of coarse cells along a block edge |
| `--overwrite` | Force regeneration even when the cache is current |

## Multiplier calculation

The configured network factors are:

| Network | Factor at an occupied 10 m pixel | Valid 100 m range |
|---|---:|---:|
| Existing pipelines | 0.25 | 0.25–1 |
| Railways | 3 | 1–3 |
| Motorways | 3 | 1–3 |

Lines have no physical width and are not buffered. Rasterio/GDAL
`all_touched=True` marks 10 m pixels touched by a line as occupied. Repeated
or overlapping features of the same network type remain binary presence;
they do not apply the factor repeatedly.

This is **coverage-weighted averaging of 10 m raster occupancy**, not an
estimate of actual infrastructure corridor area. Orientation and alignment
of a line relative to the fine grid can affect its occupied fraction. The
10 m resolution is part of the modelling convention.

For fine pixel `i`, let `p_i` be binary network presence and `w_i` be the
fraction of that pixel's area lying inside the supplied country boundary:

```text
w_i = area(fine_pixel_i intersect country) / 100 m²
f   = sum(p_i * w_i) / sum(w_i)
R   = (1 - f) * 1 + f * m = 1 + f * (m - 1)
```

The sums run over the 100 fine pixels in a 100 m cell. Fully in-country fine
pixels have weight 1, outside pixels have weight 0, and boundary pixels use
their exact polygon-intersection area. Thus boundary area weights are exact
for the supplied geometry, while network coverage still follows the 10 m
occupancy approximation. Country-exterior line portions are clipped before
rasterisation in boundary blocks so they cannot create an in-country factor.

| Occupied share of in-country area | Pipelines | Railways / motorways |
|---|---:|---:|
| 0% | 1 | 1 |
| 10% | 0.925 | 1.2 |
| 50% | 0.625 | 2 |
| 100% | 0.25 | 3 |

## Master mask and country edges

The existing 100 m population-valid mask determines eligible output cells:
1 means included; 0/NoData means excluded. Zero population remains eligible
when valid in the reference preparation. The network script applies this
mask directly and validates its alignment with the reference JSON.

Within an included boundary cell, the denominator is its in-country area.
An outside fine pixel therefore does not invalidate the whole coarse cell.
If an included coarse cell has zero in-country area, processing raises an
error because the boundary and master mask are inconsistent. Excluded cells
receive −9999 regardless of any network presence.

The country polygon defines the extent; no independent land-only mask or
coastal buffer is added. Water inside that boundary can be included. The
script does not itself remove Norway's remote islands. Choose the intended
boundary in boundary preparation, then rerun `prepare_reference_grid.py` and
all dependent resistance layers after a boundary change. A stale reference
grid is rejected rather than reused with a new boundary.

## Separate layers and later integration

Each network is averaged independently to 100 m. Multiply those factors and
the other compatible 100 m resistance layers in the integration stage.
This script does not create an integrated resistance raster.

Multiplying separately averaged factors differs from averaging products at
10 m. For example, if a pipeline and railway occupy the same 10% of a cell:

| Convention | Combined network factor |
|---|---:|
| Independently average, then multiply: `0.925 * 1.2` | 1.11 |
| Multiply within fine pixels, then average: `0.9 * 1 + 0.1 * 0.25 * 3` | 0.975 |

CO2RouteX uses the first convention here. It does not preserve subcell
co-location effects between different network types.

## Outputs, caching and memory

Outputs are placed alongside each source GPKG in its `existing_FEATURE/`
folder:

| File | Content |
|---|---|
| `COUNTRY_FEATURE_resistance_100m.tif` | Float32 factor on the master grid |
| `COUNTRY_FEATURE_resistance.json` | Method, inputs, output signatures, statistics and timings |
| `COUNTRY_FEATURE_resistance_10m.tif` | Optional fine factor TIFF |

GeoTIFFs are tiled, losslessly DEFLATE-compressed and use −9999 as NoData.
The report records valid, influenced and neutral cell counts, factor range
and mean, processing settings and elapsed time.

The optional 10 m TIFF contains `m` for occupied pixels and 1 otherwise,
only where the fine pixel has positive country area and its coarse cell is
included by the master mask. Other pixels are NoData. To reproduce the 100 m
output at country edges, use the same country-intersection weights: an
unweighted mean of the optional TIFF alone is insufficient. When `--save-10m`
is off, an old 10 m file is left untouched and `fine_output` is `null` in the
current report; do not treat that old file as a current output.

Unchanged outputs are skipped using a versioned cache. Its inputs include
the multiplier formula, configured factor, rasterisation and mask rules,
source/grid/boundary/population/mask signatures, and the optional-output
setting. Old zero-background reports do not match this method. Fingerprints
use file metadata such as size and modification time, not full content
hashes; use `--overwrite` if contents were changed without updating metadata.
Inputs are checked again before outputs are committed. Temporary files are
written first, rasters are moved into place after validation, and the JSON
report is committed last. Files written before the report are not considered
a completed matching cache entry.

Processing uses blocks of 128 × 128 coarse cells by default, corresponding
to 1,280 × 1,280 fine pixels. A one-pixel halo supports consistent line
rasterisation at block edges. Only small fine-grid blocks are held in memory;
the country's network vectors and spatial index remain in memory. Reducing
`--block-size` lowers block memory use without changing the intended method.

## Validation and methodological reference

The revised script passed 19 synthetic numerical and GIS integration checks
using Python 3.12.14, Rasterio 1.5.1, GeoPandas 1.1.4 and Shapely 2.1.2.
Checks cover known multiplier fractions, exact boundary weights (including
slivers and holes), shared-mask alignment, duplicate lines, empty networks,
identical outputs with different block sizes, weighted reconstruction from
optional fine outputs, cache invalidation, and preservation of previous
products after invalid input. Full-country datasets have not been run as
part of this validation.

For a real-data run, confirm that all eligible master-mask cells are retained,
excluded cells are NoData, and factor ranges match the table above. An empty
network must produce 1 throughout eligible cells. Check metadata and a map
overlay after changing source networks or country boundaries.

The authors' released implementation applies full network factors to
intersecting final cells. Its default grid is `0.015` degrees in EPSG:4326;
the paper describes a 1.5 km grid. Angular spacing is not a fixed 1.5 km
square. CO2RouteX instead uses 100 m projected cells and occupancy-weighted
factors. These are explicit modelling adaptations, not exact reproduction
of the authors' results. Their pipeline diameter cutoff of 500 in the
source dataset's units is not introduced here: this rasteriser uses every
supplied pipeline feature, with selection left to source preparation.

- [Bogs et al. publication](https://doi.org/10.1080/24725854.2025.2602823)
- [Bogs et al. released source code](https://zenodo.org/records/15829448)
- [Rasterio rasterisation API](https://rasterio.readthedocs.io/en/stable/api/rasterio.features.html)
