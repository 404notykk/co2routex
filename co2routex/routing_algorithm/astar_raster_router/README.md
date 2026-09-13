# CO2RouteX A* raster router

This module calculates resistance-weighted pipeline routes between connections
specified in a nodes-and-matrices Excel workbook. It supports normal routing and
measurement of computation time and process memory use.

The router is part of CO2RouteX. It does not optimise the full CCUS network or
select transport modes, pipeline capacities, or investments. Raster resistance
values are relative routing weights, not monetary construction costs.

## Requirements and installation

Use Python 3.10 or later. Select the same Python environment in your terminal
and in PyCharm.

From the repository root:

```bash
cd co2routex/routing_algorithm/astar_raster_router
python -m pip install -r requirements.txt
```

Run the commands below from this module directory unless stated otherwise.

## Input files

The router requires:

- A single study-area resistance GeoTIFF with a defined coordinate reference
  system and suitable distance units.
- An Excel workbook containing a `nodes` sheet and a directed `pipeline` matrix.

Use `examples/node_metrics_test.xlsx` as the normal-run input example and
`examples/node_metrics_benchmark.xlsx` for the Netherlands benchmark. Preserve
the column layout and ensure matrix identifiers match the node identifiers.
Non-zero pipeline-matrix entries request routing; zero entries do not.

Country resistance rasters, including compressed and uncompressed versions,
are supplied separately. Place them in:

```text
co2routex/database/pipeline/spatial_cost_resistance/
```

The default normal-run configuration expects
`spatial_resistance_NL_cropped.tif`. Adjust the input paths for other datasets.
The repository's ignore rules exclude the supplied rasters and generated output
folders from new Git additions.

## Run the router

In PyCharm, run `run_router.py`. Its `CONFIG_PATH` defaults to `config.test.yaml`.
Alternatively:

```bash
python run_router.py --config config.test.yaml
```

`config.yaml` provides an alternative normal-run configuration:

```bash
python run_router.py --config config.yaml
```

Paths inside a YAML configuration resolve relative to that YAML file. The
supplied normal-run configuration uses:

```yaml
raster_path: "../../database/pipeline/spatial_cost_resistance/spatial_resistance_NL_cropped.tif"
workbook_path: "examples/node_metrics_test.xlsx"
output_dir: "output"
nodes_sheet: nodes
pipeline_sheet: pipeline
mode_sheets: [pipeline]
nodes_crs: EPSG:4326
connectivity: 8
prevent_corner_cutting: true
snap_radius_cells: 0
zero_is_barrier: false
cache_max_routes: 0
cache_reverse_routes: false
rss_sample_interval_s: 0.05
output_workbook_name: node_metrics_routed.xlsx
routes_filename: routes.gpkg
```

## Routing behaviour

The raster is treated as a graph of traversable cell centres. Movement cost
between adjacent cells is their centre-to-centre distance multiplied by the
mean resistance of the two cells. The A* heuristic uses straight-line distance
multiplied by the global minimum traversable resistance.

The efficiency update prepares shared raster information once per run and
reuses it across requested connections. Float32 raster values remain Float32 in
memory, while accumulated search costs use double-precision arithmetic. Searches
operate over the valid raster rather than a restricted per-pair search window.

Nodes must lie in valid raster cells. Keep `snap_radius_cells: 0`; invalid or
out-of-bounds nodes cause an error before routing. A valid node maps to its
containing cell centre. The offset from the original coordinate to that centre
is reported separately and excluded from the routed length.

Route caching is disabled in the supplied configurations. If enabled through
`cache_max_routes`, the cache is bounded by route count. For timing experiments,
keep `cache_max_routes: 0` and `cache_reverse_routes: false` to measure fresh
searches rather than cached results.

The prepared-grid interface is `search_prepared(...)`. The independent
`astar_search(...)` interface validates its input before searching. Recreate a
prepared grid if its raster values or validity mask change.

## Outputs and interpretation

The normal routing workflow writes the following files to its configured output
directory:

| File | Contents |
| --- | --- |
| `node_metrics_routed.xlsx` | Copy of the input workbook with successful pipeline connections updated to kilometres and route metrics added |
| `routes.gpkg` | Successful route geometries in the raster CRS, in layer `pipeline_routes` |
| `benchmark_results.xlsx` | Timing, routing, memory, settings, and field-definition tables |

Output names for the routed workbook and GeoPackage can be configured. For
repeated benchmark runs, consult [README_TIMING.md](README_TIMING.md) and the
selected benchmark configuration rather than assuming normal-run folder layout.

The performance workbook is documented with these sheets:

| Sheet | Purpose |
| --- | --- |
| `Run_summary` | Shared setup, routing and output times, run counts, and process-memory measurements |
| `Pair_results` | Connection identifiers, statuses, route distances and costs, and search statistics |
| `Pair_runtime` | Per-connection timing components and totals |
| `Memory` | Process RSS at pair start and sampled peak RSS during that pair |
| `Settings` | Input and raster properties, configuration, and execution-environment details |
| `Field_guide` | Field definitions, units, measurement scopes, and overlap notes |

Use the workbook's field guide for exact column meanings. Times are in seconds
and memory in MiB unless stated otherwise. Route distances are in kilometres
unless a field explicitly names another unit.

Connections without a valid path receive `status: no_path`. Their original
matrix request values remain unchanged; check route status before treating a
matrix value as a computed distance. Invalid input nodes abort the run.

The current cell-offset fields are `from_cell_center_offset_m` and
`to_cell_center_offset_m`. Update downstream tools that still expect
`from_snap_distance_m` and `to_snap_distance_m`. Current performance reporting
uses XLSX tables; older CSV filenames and timing fields should not be assumed
to apply to this version.

## Benchmarking

Run the benchmark driver from this module directory:

```bash
python run_distance_benchmark.py --config config.benchmark.yaml
```

Country-specific configurations support NL, DE, and NO comparisons using
separately supplied cropped LZW and uncompressed rasters. See
[README_TIMING.md](README_TIMING.md) for input selection, comparison controls,
and interpretation of timing and memory results.

## Tests

From this module directory, in the project Python environment:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```

The updated test module is `tests/test_updated_router.py`. Automated tests support
correctness checks; they do not establish performance on country-scale datasets.
Measure runtime and memory on the intended inputs and execution environment.

## Limitations

- Routes are planning-level raster paths, not construction-ready alignments.
- Resolution and valid-cell connectivity constrain the possible routes.
- All-pairs routing is sequential in this update.
- Route geometry records remain in memory until export. Large sets of long
  routes can therefore increase memory use even with caching disabled.
- Sampled process memory can miss short peaks; it is not a guaranteed minimum
  RAM requirement.
- Changes to output fields or input validation may require updates to downstream
  scripts and existing configurations.
