# CO2RouteX water buffer

## Status and scope

`prepare_water_buffer.py` prepares the optional **vector water buffer** for
NL, DE and NO using Overture's OSM-derived water features. All selected water
is combined into one modelling domain and will use the same resistance scheme.
Swimming pools and wastewater/sewage features are excluded. This first script
prepares geometry and inspection outputs.

`rasterise_water_buffer_domain.py` then reuses those vector outputs to create
the 100 m buffer eligibility mask, a water-fraction diagnostic, and the constant
water and population resistance layers.

`rasterise_water_buffer_slope.py` derives slope in degrees and its resistance
multiplier from numerical EMODnet DTM 2024 NetCDF subsets. It writes only inside
the existing buffer mask and leaves unsupported slopes as NoData. Protected
areas, the three network layers, integration, route validation and optional
composition remain later steps.
The vector workflow needs no LCM inputs. Existing LCM mosaics stay in
`raw/water/` for the original country water-resistance workflow. No additional
LCM tiles, extracts or intermediate LCM files are required for this buffer.

For the supplied country polygon `P`, the rule is:

```text
outer_strip = buffer(P, 5000 metres) minus P
water_extension = outer_strip intersect selected mapped water polygons
```

Distance and area calculations use EPSG:3035. The extension includes mapped
sea, lakes and river surfaces outside the country boundary; it does not require
connection to the sea. Neighbouring-country land is excluded by retaining only
water polygons. Polygon holes preserve islands where those holes are present
in the source. Existing holes in the country boundary also contribute where
the buffer reaches into them. The supplied boundary determines which Norwegian
islands are included; this module does not change that selection.

The original country preparation, rasterisation and integration scripts and
outputs remain unchanged. This module is optional and developed step by step.

## Installation and input locations

Place these files in the inner `co2routex` package:

```text
co2routex/data_processing/spatial_resistance/water_buffer/
    prepare_water_buffer.py
    rasterise_water_buffer_domain.py
    rasterise_water_buffer_slope.py
    README_water_buffer.md
```

The inner `co2routex` directory also contains `database`, `data_processing` and
`visualisations`. Use Python 3.10 or newer in the project's GIS environment:

```bash
python -m pip install numpy pandas geopandas pyogrio 'shapely>=2.1' pyproj pyarrow matplotlib rasterio affine netCDF4 'overturemaps==1.0.2'
```

All paths below are relative to `database/pipeline/`.

| Required existing input | Default location | Purpose |
|---|---|---|
| Current country boundary | `intermediate/boundaries/COUNTRY.shp` and companions | Geometry to extend |
| Existing reference JSON | `intermediate/reference_grid/COUNTRY_reference_grid.json` | The 100 m grid anchor and nested 10 m convention |

These are the only project datasets that must already exist for stage 1.
With internet access and the dependencies above installed, the vector script
downloads Overture water features automatically and creates
`raw/water_buffer/overture/RELEASE/` to store them. **That folder and its water
files do not need to exist before the first run.** They are new vector data,
unrelated to the LCM mosaics already in `raw/water/`.

`COUNTRY` is `NL`, `DE` or `NO`. The vector script reads neither the population TIFF
nor the population-valid mask. Existing resistance rasters, LCM rasters and
EMODnet bathymetry are not inputs to stage 1. The reference JSON supplies alignment; the
output extent may extend beyond its original dimensions.

## Stage 1: prepare the vector water buffer

From the new `water_buffer` script folder:

```bash
python prepare_water_buffer.py
```

Running the file directly in PyCharm with no arguments processes **NL, DE and
NO**, in that order. The script derives request extents automatically; no manual
tile coordinates are needed. The equivalent explicit command is:

```bash
python prepare_water_buffer.py --countries NL DE NO
```

The default release is `latest`. It is resolved once per run to an exact
Overture release, used for every selected country and written to the report.
For a reproducible rerun, specify that recorded release explicitly:

```bash
python prepare_water_buffer.py --release 2026-08-19.0
```

That is the release used in the successful NL run. This command processes all
three countries using that release and reuses the existing NL source cache if
its request and checksum still match. The revised filtering is always applied
again; an existing cache does not preserve the previous pool/sewage selection.
Keep the release used for the study rather than automatically changing it later.
An exact release with a matching cache can be reused without resolving the
latest release online.

For one country only, use `--countries NL` (or `DE` or `NO`). All requested
countries must have their boundary files and reference JSON available.

If the script is outside the intended project folder, supply the pipeline path:

```bash
python prepare_water_buffer.py --countries NL --pipeline-dir /path/to/co2routex/database/pipeline
```

Optional offline mode, only if an Overture vector extract has already been
obtained (skip this option for the normal first run):

```bash
python prepare_water_buffer.py --countries NL --water-file /path/to/water_extract.parquet
```

Use an Overture-compatible GeoParquet with a declared CRS and a `subtype`
column. If present, `theme` and `type` must identify `base` and `water`.
`class`, `is_intermittent` and `source_tags` are optional; their absence is
recorded. A local file must cover the complete request area; the presence of
features alone cannot establish coverage. `--water-file` accepts one country
per run. Its report records local input provenance with unverified extraction
coverage; preserve the release and extraction metadata alongside the file.
Do not pass an LCM TIFF to `--water-file`; this option reads vector GeoParquet.

| Option | Meaning |
|---|---|
| `--countries NL DE NO` | Any subset; defaults to all three countries |
| `--buffer-km 5` | Positive extension distance in kilometres |
| `--release RELEASE` | Exact Overture release, or `latest` resolved once |
| `--water-file PATH` | Existing water GeoParquet; one country per run |
| `--include-intermittent` | Also admit explicitly intermittent surfaces; excluded by default |
| `--pipeline-dir PATH` | Override the pipeline directory |
| `--boundary-dir PATH` | Override the country shapefile directory |
| `--reference-dir PATH` | Override the reference JSON directory |
| `--output-root PATH` | Scenario root; country/domain folders are created underneath |

## Automatic extraction and reproducibility

The script calculates a longitude/latitude bounding box around the extended
country geometry and requests Overture `base/water` features intersecting it.
The box also contains country interiors and neighbouring land; the subsequent
geometric intersection selects only the outer strip. Network traffic is not
necessarily restricted to that exact narrow strip.

The Overture client accesses cloud-hosted GeoParquet and can use S3 internally.
No manual LCM block management is needed. The supplied revised vector reports
record about 105 seconds for NL with its cached extract, 627 seconds for DE,
and 8,477 seconds for NO. The raw extracts are about 357 MB, 962 MB and 2.12 GB
respectively. These are stage-1 measurements on the user's server, not runtime
estimates for stage 2. Geographic selection and streaming are supported, but
local clipping and plotting still require memory. See the
[Overture Python client documentation](https://docs.overturemaps.org/getting-data/overturemaps-py/).

Caches are separated by exact release and request extent:

```text
raw/water_buffer/overture/RELEASE/
    COUNTRY_water_BBOXDIGEST.parquet
    COUNTRY_water_BBOXDIGEST.source.json
```

The extract is retained for reuse. Provenance records the request, client
version, row count, bytes, download time and a SHA-256 digest. Reuse validates
the request, digest and row count. Keep the data and sidecar together; an
incomplete pair or checksum mismatch stops processing. A failed or empty
download is not accepted as evidence that the strip contains no water.

## Water selection and geometric method

1. Validate current country polygons and repair their topology in memory where
   possible. Dissolve them in EPSG:3035.
2. Construct the 5 km outer strip and calculate the download extent.
3. Obtain a cached, downloaded or explicitly supplied water extract.
4. Retain selected water-surface polygons. Exclude points, lines and physical
   naming features, swimming pools, wastewater and sewage features. Do not
   manufacture river width by buffering centrelines.
5. Exclude features explicitly marked `is_intermittent=true` by default, and
   explicit `natural=wetland` source tags where present. Retain and count
   missing intermittency values; absence does not prove permanence.
6. Repair water topology where possible, reproject, intersect with the strip
   and union overlapping geometry. Union prevents double-counting area and
   removes artificial seams between adjacent ocean tiles.
7. Save geometry, an area/provenance report and inspection figures. Record the
   expanded extent on the existing 100 m lattice for subsequent work.

Overture combines processed ocean polygons from OSM coastlines with inland
water features. Its water theme also contains points, lines and `physical`
naming features; these are not all water surfaces. Ocean areas may be tiled,
so a tile edge is not a shoreline. See the [Overture water schema](https://docs.overturemaps.org/schema/reference/base/water/).

The exclusions are checked against both the feature class and subtype, and
clear equivalents in retained OSM tags. A wastewater surface is excluded even
when its subtype is `reservoir`; ordinary reservoirs, canals and non-wastewater
basins remain eligible. These are feature-selection rules, not a blanket
removal of all artificial water. An excluded feature cannot add water area;
independently accepted overlapping water polygons remain eligible.
Unknown subtypes or classes are excluded and
reported; a missing class is admitted with a recognised subtype unless an
explicit exclusion is present. Eligibility does not establish engineering
feasibility for every selected body.

The modelling geometry and area totals use a single union of all admitted
water. The inspection map retains blue and teal as visual cues, including teal
for lakes and rivers in the otherwise beige border strip. These colours never
change domain inclusion or resistance rules. The display grouping comes from
source categories and is not an independent determination of salinity.

JSON areas from this stage come from vector geometry in EPSG:3035. They differ
from the LCM checker's 10 m centre-sampled estimates. This script records
alignment; the following raster stage implements the agreed cell-centre rule.

## Outputs and reruns

Each country has its own `domain/` folder. For NL:

```text
database/pipeline/intermediate/water_buffer/5km/NL/domain/
    NL_water_buffer.gpkg
    NL_water_buffer.json
    NL_water_buffer_overview.png
    NL_water_buffer_overview.pdf
```

The GeoPackage has four EPSG:3035 layers: `country_boundary`, `outer_strip`,
`water_buffer` and `not_selected_as_water`. `water_buffer` is the single
geometry for subsequent processing; overlapping water polygons count once.
The new report uses schema version 2 and has one water-buffer area total.

The JSON records vector areas, water share of the strip, geometry checks,
source filters and rejection counts, missing attribute diagnostics, source
fingerprints, download provenance, runtime and aligned grid metadata. Missing
intermittency area can overlap polygons with explicit flags; it describes
source uncertainty and is not a separate area partition.

The three-panel inspection plot shows an overview and water detail views.
Both water colours belong to the same modelling buffer. PNG is saved at 300 dpi; PDF
retains vector geometry for zooming. Use the
GeoPackage for detailed inspection in QGIS or ArcGIS Pro. Figures illustrate
the source selection; they do not validate thematic completeness.

Successful reruns replace these four outputs after staged writing, with the
JSON written last. The entire GeoPackage is replaced, so the old separate
water-category layers do not remain. No manual output deletion is needed.
Replacement is atomic per file, not for the four-file set.
Run one process per country/output location at a time. Cached source extracts
are reused when the release, request and checksum match.

## Stage 2: rasterise the buffer domain

Run this stage after the vector outputs have been generated for the requested
countries. It reuses their GeoPackages, with no new Overture download or
reprocessing of the raw water extracts:

```bash
python rasterise_water_buffer_domain.py
```

No arguments, including direct execution in PyCharm, processes **NL, DE and
NO** in order. For an explicit selection:

```bash
python rasterise_water_buffer_domain.py --countries NL DE NO
```

Place this new script beside `prepare_water_buffer.py`. It is standalone and
does not import the preparation script. It requires numpy, geopandas, pyogrio,
shapely 2.1 or newer, pyproj, rasterio and affine. The original country scripts
and their outputs remain unchanged.

Inputs, relative to `database/pipeline/`:

| Input | Default location |
|---|---|
| Prepared polygons | `intermediate/water_buffer/5km/COUNTRY/domain/COUNTRY_water_buffer.gpkg` |
| Vector report, schema 2 | `intermediate/water_buffer/5km/COUNTRY/domain/COUNTRY_water_buffer.json` |
| Current reference grid JSON | `intermediate/reference_grid/COUNTRY_reference_grid.json` |

The GeoPackage supplies `water_buffer`, `country_boundary` and `outer_strip`.
These are the saved geometry snapshots from stage 1. This stage does not open
the current boundary shapefiles, so a later change to those source boundaries
requires regenerating the vector buffer first; it cannot detect that change
from the snapshots alone.
Population values and the population-valid mask are not needed. Water geometry
already excludes the supplied country polygon. Existing valid country raster
cells will receive priority in the later optional composition stage; this
script does not read or overwrite them.

### Cell eligibility and water fraction

The main eligibility rule is **the 100 m cell centre lies strictly inside the
combined water-buffer polygon**. The centre is tested directly against the
vector geometry. A centre exactly on any water-polygon boundary is excluded;
this provides a deterministic rule for ties. Blue and teal water are treated
as one geometry.

Separately, sample the centres of the 100 nested 10 m cells and calculate:

```text
estimated_water_fraction = number of 10 m sample centres inside water / 100
```

The fraction is a diagnostic, not an eligibility threshold. In particular,
the 100 m centre lies between four 10 m sample centres; it is not represented
by selecting a single central fine cell. A narrow water polygon can contain
the 100 m centre while missing every fine sample, giving mask 1 and fraction
0. Conversely, a tiny hole at the 100 m centre can give mask 0 and fraction 1.
The report counts these cases rather than silently changing eligibility.
Fractions have increments of 0.01, but this does not establish 1% accuracy of
the actual polygon area.

Eligible cells receive **water multiplier 10** and **population multiplier 1**.
Other cells receive NoData in those resistance rasters. Do not calculate
`10 * fraction + 1 * (1 - fraction)`: the excluded portion is not neutral
traversable land. No slope, protected-area or network values are assumed here.

### Raster outputs

For each country, the five outputs are stored under
`intermediate/water_buffer/5km/COUNTRY/raster/`:

| File | Meaning |
|---|---|
| `COUNTRY_water_buffer_mask_100m.tif` | uint8: 1 eligible, 0 excluded; reserved NoData 255 |
| `COUNTRY_water_buffer_fraction_100m.tif` | float32: estimated extension-water fraction from 0 to 1 |
| `COUNTRY_water_buffer_water_resistance_100m.tif` | float32: 10 in eligible cells, otherwise NoData -9999 |
| `COUNTRY_water_buffer_population_resistance_100m.tif` | float32: 1 in eligible cells, otherwise NoData -9999 |
| `COUNTRY_water_buffer_raster.json` | Grid, policies, input hashes, diagnostics and runtime |

The new raster report uses its own schema version 1. It is a different report
type from the unchanged schema-2 vector report in `domain/`.

All rasters share the expanded EPSG:3035 100 m grid recorded by stage 1.
The mask and fraction cover the full output rectangle: their zero values are
meaningful, including inside the original country and outside selected water.
Use **`mask == 1`** to identify eligible buffer cells; the GeoTIFF NoData mask
alone is insufficient because zero is a valid stored mask value.
The fraction raster reserves -9999 for NoData, but a successfully processed
rectangle contains valid fractions throughout. A fraction of zero describes
the sampled extension geometry, not proof that the physical location is land.

Processing uses blocks and skips fine sampling where no water can intersect.
No country-wide 10 m array or persistent 10 m raster is created. Outputs use
tiled, compressed GeoTIFFs. Files are staged, then replaced after successful
processing, with the JSON report last. Replacement is atomic per file, not
across the entire set; run one writer per country/output location.

### Checks, diagnostics and options

Required paths for all requested countries are checked before processing.
The script checks report identity, CRS, valid polygon geometry, containment
within the outer strip, exclusion of country area, reported areas, 100 m
alignment and exact 10 m nesting. It records SHA-256 hashes of the inputs;
file modification times alone cannot invalidate a run.

The vector report records the old reference JSON hash, but does not contain a
snapshot of its original dimensions. A changed reference-file hash is therefore
recorded for inspection; current grid semantics, offsets, strip bounds and
reference-extent containment must still pass. This checks compatibility and
does not establish that every historical reference metadata field is unchanged.
The vector GeoPackage is checked geometrically and hashed for future tracing;
the old vector report has no output-file hash with which to authenticate its
historical bytes.

The report distinguishes exact vector water area, estimated water area from
10 m samples, and the footprint of eligible 100 m cells. The last quantity
includes whole cell squares and must not be reported as exact water area.
It also records partial cells, centre/fraction disagreements, and a comparison
with the alternative `fraction >= 0.5` mask. That comparison is diagnostic
only; it does not change the output eligibility mask or constitute a routing
sensitivity experiment.

| Option | Meaning |
|---|---|
| `--countries NL DE NO` | Any subset; defaults to all three |
| `--pipeline-dir PATH` | Override the pipeline directory |
| `--domain-root PATH` | Scenario root containing `COUNTRY/domain/` inputs |
| `--reference-dir PATH` | Override the reference JSON directory |
| `--output-root PATH` | Scenario root containing `COUNTRY/raster/` outputs |
| `--buffer-km 5` | Select the distance scenario; must match the vector report |
| `--block-size 128` | Processing block edge in 100 m cells, from 1 to 512; does not change resolution |

### Routing and remaining layers

A centre inside water does not guarantee that a route segment connecting it to
an adjacent centre stays inside the permitted domain. This raster stage does
not validate route segments, connectivity, or engineering feasibility. Before
using the extension for routing, check links or resulting route sections
against the permitted country-plus-water geometry, retaining the original
missing-data barriers. Narrow rivers may disappear at 100 m; local finer
routing may be needed where that changes the result. Representative route
comparisons with a majority-water mask remain a later sensitivity check.

Stage 2 produces water and population factors only. Stage 3 below adds slope;
the seven-factor product is still a later step. Absent bathymetry is not
assigned a flat slope or a neutral penalty. Integration must account explicitly
for unresolved coverage in the remaining layers.

## Stage 3: rasterise water-buffer slope

Run this after the raster-domain stage, from the same script folder:

```bash
python rasterise_water_buffer_slope.py
```

Running directly in PyCharm with no arguments processes **NL, DE and NO**.
For one country, use `--countries NL`. The script performs no downloads and
does not need to rerun vector or domain preparation. Its direct dependencies
are `numpy`, `rasterio`, `affine`, `pyproj` and `netCDF4`:

```bash
python -m pip install numpy rasterio affine pyproj netCDF4
```

### Inputs and outputs

For each `COUNTRY`, all paths below are relative to `database/pipeline/`:

| Input | Location |
|---|---|
| Numerical EMODnet elevation subset | `raw/bathymetry/COUNTRY_emodnet_dtm_2024.nc` |
| Completed raster-domain report | `intermediate/water_buffer/5km/COUNTRY/raster/COUNTRY_water_buffer_raster.json` |
| Existing buffer eligibility mask | `intermediate/water_buffer/5km/COUNTRY/raster/COUNTRY_water_buffer_mask_100m.tif` |

The NetCDF files sit directly in **`raw/bathymetry/`**. The script does not
search a release subfolder or silently choose among several candidate files.
The stage-2 report and mask define the output grid; neither a population raster
nor the diagnostic water-fraction raster is read.

All three new outputs go beside the existing mask in
**`intermediate/water_buffer/5km/COUNTRY/raster/`**:

| Output | Contents |
|---|---|
| `COUNTRY_water_buffer_slope_degrees_100m.tif` | Derived slope angle in degrees |
| `COUNTRY_water_buffer_slope_resistance_100m.tif` | `1 + 19 * slope_degrees / 90` |
| `COUNTRY_water_buffer_slope.json` | Method, input fingerprints, coverage, statistics and timing |

Both TIFFs use the existing EPSG:3035 grid exactly, Float32 and **NoData =
−9999**. Eligible cells receive results only when their slope neighbourhood
has usable elevation data. Excluded cells remain NoData. There is no separate
`slope/` folder, and no existing water, population or country raster is changed.

### From elevation to slope

1. Validate the source `elevation(latitude, longitude)` variable, metre units,
   LAT reference, WGS84 coordinates and regular native spacing. Native grid
   interval is **1/960 degree = 0.0625 arc-minute = 3.75 arc-seconds**. Latitude
   and longitude can each run in either direction; both are handled explicitly.
2. Transform the aligned EPSG:3035 100 m cell centres to longitude/latitude.
   Interpolate elevation bilinearly from the four enclosing native source
   centres, requiring **all four to be finite and strictly below 0 m LAT**.
   Unsupported locations remain missing; weights are never renormalised around
   missing or screened-out samples. Even a contributor with zero interpolation
   weight must pass this deliberately conservative neighbourhood rule.
3. Read a one-cell target halo around each processing block and calculate the
   **Horn 3 × 3 gradient** with 100 m projected horizontal spacing. All nine
   interpolated target centres must be usable. Halo elevations can lie outside
   the eligible buffer: the buffer mask is applied after differentiation.
4. Convert gradient magnitude to degrees and then to the slope multiplier:

   ```text
   slope_degrees = degrees(atan(sqrt((dz/dx)^2 + (dz/dy)^2)))
   slope_multiplier = 1 + 19 * slope_degrees / 90
   ```

5. Write results only where the existing 100 m eligibility mask equals 1.
   Blocks with no eligible cells skip bathymetry reads and write NoData.

EMODnet elevations are already in metres. **Do not apply the EU-DEM slope-DN
conversion** to them: this step derives an angle from elevation differences.
The 100 m grid is the analysis grid, not a claim of extra bathymetric detail;
there is no 10 m slope interpolation or new bathymetry information at that scale.
The diagnostic water fraction does not dilute the slope multiplier or change
eligibility. Blue and teal mapped water receive the same calculation.

### What missing values mean

The numerical source includes bathymetry and topography. Its negative-LAT
screen is a conservative operational choice, **not verification of bed data**:

- Zero or positive elevation, source NoData, insufficient source extent, or
  an incomplete derivative neighbourhood leave the slope and multiplier missing.
- Some legitimate intertidal bed and above-LAT lake beds can be excluded by
  this rule. Inland water may have no bed survey in this product.
- Negative elevation can also occur on land below LAT. Coastline mismatch or
  source limitations are not eliminated simply by checking the sign.
- These elevation-only NetCDF subsets do not carry all the source-reference
  or quality layers needed to establish local survey provenance. The EMODnet
  product itself may contain interpolated or fallback elevations; this script
  performs **no additional missing-data filling**.

Read report coverage as **numerical coverage after the negative-LAT screen**,
with the eligible buffer cells as denominator. It must not be called verified
seabed coverage. A completed run can have partial coverage or no usable slopes.
An empty eligible domain or zero usable slopes produces explicit report status
and all-NoData rasters, not fabricated flat terrain. The existing eligibility
mask remains unchanged. Do not silently replace missing slopes with multiplier 1.

The report separates centre-support failures from failures of the surrounding
3 × 3 neighbourhood, then reports valid slope and multiplier statistics. Centre
failures use a fixed priority: outside complete source support, missing source
values, then nonnegative support. Separate zero/positive contributor diagnostics
can overlap and must not be added as mutually exclusive categories. Statistics
and slope-class percentages refer to valid slope cells; coverage refers to all
eligible cells.

The multiplier is an **assumed transfer of the terrestrial 1–20 penalty** to
water-bed slope. The cited engineering studies motivate accounting for seabed
slope; they do not calibrate this linear cost law. Keep this assumption in the
research methods and consider sensitivity analysis. LAT is spatially varying,
and EPSG:3035 distances have projection distortion; derived slopes are an
approximation at the analysis scale, not engineering survey gradients.
EU-DEM-derived slope inside the original country domain remains unchanged;
no gradient is calculated across a stitched EU-DEM/EMODnet seam.

### Validation, provenance and options

Before output writing, inputs for every selected country undergo metadata
preflight. The script checks country/scenario/report identity, grid dimensions,
100 m north-up EPSG:3035 alignment and fine-grid nesting, and mask alignment.
Mask pixel codes and eligible-cell counts are checked during staged processing,
before replacing previous outputs. It records SHA-256 hashes for
the current source NetCDF, mask and stage-2 report. Modification times are
informational, so file-copy timestamp rounding does not itself invalidate inputs.

The stage-2 report does not contain a historical hash of its output mask.
Matching grid, mask codes and counts therefore establishes consistency, but
cannot prove that the mask geometry has never changed. Stage 3 fingerprints
the current mask so later stages can check exactly which bytes were used.

Outputs are staged and replaced after successful processing, with the JSON
last. Replacement is atomic per file, not for the whole set; use one writer
per country/output folder. The computation reads source windows and processes
blocks rather than allocating countrywide bathymetry arrays.

| Option | Meaning |
|---|---|
| `--countries NL DE NO` | Any subset; defaults to all three |
| `--pipeline-dir PATH` | Override the pipeline directory |
| `--bathymetry-dir PATH` | Override the directory containing the named NetCDF files |
| `--buffer-root PATH` | Scenario root containing `COUNTRY/raster/` inputs |
| `--output-root PATH` | Alternative scenario root for `COUNTRY/raster/` outputs; defaults to the input buffer root |
| `--buffer-km 5` | Select the distance scenario; must match the stage-2 report |
| `--block-size 128` | Processing edge in 100 m cells, from 1 to 512; no resolution change |

For example, when running a downloaded script outside the project folder:

```bash
python rasterise_water_buffer_slope.py --countries NL DE NO --pipeline-dir /path/to/co2routex/database/pipeline
```

## Interpretation and scientific limits

The retained geometry means **water mapped by the selected source and rules**.
The remainder of the strip is not admitted to the extension. It must not be
described as independently confirmed land: an unmapped lake, missing river
surface or omitted coastline feature can fall there. A successful download
establishes that the request completed, not that the water inventory is complete.

OSM coastlines follow mean high-water springs, so their ocean-side area can
include intertidal ground. This differs from LCM's permanent-water class.
Switching sources changes the water definition as well as data format.
Excluding intermittent features does not remove intertidal area already
within ocean polygons. See the [OSM coastline definition](https://wiki.openstreetmap.org/wiki/Tag:natural%3Dcoastline).

Inspect coastlines, islands and shared land borders. For NL, check the Wadden
islands, estuaries and border rivers. A river represented only by a centreline
contributes no surface. Administrative boundaries can differ from detailed
water boundaries; the supplied country polygon still defines the original
domain. An NL inspection cannot certify completeness in DE or NO. Keep the
release, selection rules, limitations and any manual exclusions traceable.

## Historical reason for changing the water source

An earlier audit of the existing LCM mosaics gave the following results for
the 5 km outer strips. These results document the change of source; the old
coverage checker is not part of the current workflow and need not be retained
in the active script folder.

| Country | Outer strip classified | Confirmed water (km²) | Unknown (km²) |
|---|---:|---:|---:|
| NL | 27.0502% | 26.133 | 6,387.178 |
| DE | 9.5065% | 62.825 | 22,480.579 |
| NO | 99.9783% | 68,855.075 | 18.093 |

Classified coverage includes recognised water and non-water across the entire
strip. The NL/DE results strongly suggest boundary-clipped or masked mosaics:
the neighbouring-country mosaic supplied most classified exterior coverage.
This is an inference from the reports, not independent confirmation of raster
processing. Unknown area must not be treated as water or land. All three
reports have `coverage_complete=false`.

Keep the three `COUNTRY_water_buffer_coverage.json` reports as provenance if
desired. They and the earlier diagnostic TIFFs are not inputs to
`prepare_water_buffer.py`.

## EMODnet source and download provenance

Stage 3 uses numerical regional subsets from the
[EMODnet DTM 2024 ERDDAP dataset](https://erddap.emodnet.eu/erddap/griddap/bathymetry_dtm_2024.html),
selecting `elevation`, native stride **1**, and NetCDF output. WMS colour images
cannot supply numerical gradients. The script accepts the elevation-only
regional files already downloaded, with these exact names:

```text
raw/bathymetry/
    NL_emodnet_dtm_2024.nc
    DE_emodnet_dtm_2024.nc
    NO_emodnet_dtm_2024.nc
```

The regional requests proposed for these domains include a margin beyond the
mapped-water extent for interpolation and slope neighbours:

| File prefix | Longitude bounds | Latitude bounds |
|---|---|---|
| NL | 2.8 to 7.6 | 50.4 to 53.8 |
| DE | 5.2 to 15.8 | 47.0 to 55.3 |
| NO | 1.2 to 32.6 | 57.0 to 72.1 |

Actual coverage is checked during processing; these rectangles do not guarantee
bed data within every eligible water cell. ERDDAP query dimensions are latitude
then longitude. Keep the downloaded metadata, release and query for provenance.

The [numerical dataset metadata](https://erddap.emodnet.eu/erddap/info/bathymetry_dtm_2024/index.html)
specify `elevation` in metres relative to Lowest Astronomical Tide (LAT), with
negative values below the reference level and positive values above. Do not mix
LAT and Mean Sea Level products. The coordinate interval is **0.0010416667
degree**, equivalent to **0.0625 arc-minute**, despite the arc-second label seen
in the portal. Geographic spacing is not a uniform metric resolution. See also
the [EMODnet bathymetry product description](https://emodnet.ec.europa.eu/en/bathymetry).

## Agreed subsequent steps

The vector, domain-raster and buffer-slope stages above are implemented.
Remaining work is:

- Apply the agreed area-weighted protected-area method to spatially
  intersecting polygons from all realms, including freshwater areas.
- Extract motorways, railways and pipelines from sources covering the
  extension, then calculate their separate resistance layers.
- Review the slope reports and assess bathymetry and derived-slope gaps
  separately. Marine coverage does not guarantee bed data for every lake or
  river. The fallback remains
  unagreed; it is not implicitly zero slope or multiplier 1.
- Multiply the seven buffer factors and optionally compose with the existing
  integrated raster into a separate output. Existing valid integrated cells
  retain priority.

## Research references and validation scope

- [Copernicus LCM 2020, 10 m, version 1](https://doi.org/10.2909/602507b2-96c7-47bb-b79d-7ba25e97d0a9) and its [product manual](https://land.copernicus.eu/en/technical-library/copy_of_product-user-manual-global-land-cover-10-m/@@download/file).
- [EMODnet DTM 2024](https://doi.org/10.12770/cf51df64-56f9-4a99-b1aa-36b8d7b743a1).
- [GDAL terrain-processing documentation](https://gdal.org/en/stable/programs/gdaldem.html), including Horn slope and neighbourhood handling; this script implements its own explicit sampling and derivative policy.
- [Seyfipour, Mirghaderi & Bahaari (2023), stability on sloping seabeds](https://doi.org/10.1007/s40722-022-00245-y).
- [Tian et al. (2022), axial pipeline walking and seabed slope](https://doi.org/10.1680/jgeot.20.P.135).
- [Holden et al. (2005), Ormen Lange routing and seabed preparation](https://publications.isope.org/proceedings/ISOPE/ISOPE%202005/papers/123_HM_02.pdf).

The revised vector-preparation script passed 52 synthetic tests in Python 3.12, covering geographic
selection, island holes, overlaps, facility exclusions, ordinary-water retention,
intermittency, alignment, unified outputs and rerun replacement, the three-country
default, and mocked download/cache failures. The existing NL cache key remains
compatible. A labelled synthetic map was also visually inspected. These checks
do not certify map completeness or production-country performance.

The new raster-domain stage passed 35 synthetic tests in Python 3.12. These
check centre selection independently of fine sampling, exact boundary ties,
holes and narrow water, analytic fractions, block-size invariance, alignment,
NoData and constant factors, all-country defaults, input validation and
preservation of previous outputs when processing fails. Artificial examples
were also inspected visually.

Subsequent revised vector runs for NL, DE and NO succeeded on the user's server;
the supplied schema-2 reports and inspection maps were reviewed. Their combined
water areas are 4,626.913, 10,250.764 and 68,772.545 km² respectively. These are
vector areas, before the centre-based raster selection. No further vector rerun
is needed to use those outputs in stage 2.

The subsequent stage-2 reports supplied for all three countries also show
successful runs, with these eligible-cell counts and reported processing times:

| Country | Eligible 100 m cells | Runtime |
|---|---:|---:|
| NL | 462,586 | 47 seconds |
| DE | 1,024,984 | 2 minutes 22 seconds |
| NO | 6,876,883 | 19 minutes 46 seconds |

Those JSON reports were checked for internal consistency, grid alignment and
provenance. The production TIFFs and full-country NetCDF files are not available
in this development workspace, so this does not constitute inspection of their
pixel values or a completed production slope run.

The buffer-slope stage passed **44 tests** in Python 3.12, covering synthetic
metric planes, both axis directions, missing/nonnegative source support, block
boundaries, report accounting, metadata validation and failure preservation.
A separate end-to-end check used a real
20 × 20 EMODnet NetCDF subset and an artificial aligned 5 × 5 eligibility mask:
all 24 eligible cells produced finite slopes and the expected multiplier
conversion. This checks the real download format and calculation path, not
full-country coverage or bed-survey accuracy.

Python 3.10 syntax is checked separately from execution in the development
environment.
