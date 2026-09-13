# Pipeline routing performance benchmarks

This guide describes how to compare routing computation time and memory use
across node-pair distances and raster compression variants. See
[README.md](README.md) for installation, normal routing, and output interpretation.

## Working directory and environment

Run the examples from:

```text
co2routex/routing_algorithm/astar_raster_router/
```

Use the same Python interpreter for dependency installation and execution.
For comparisons, keep the Python environment and relevant GDAL settings
consistent and record the machine and software versions.

## Prepare the benchmark inputs

Resistance rasters are supplied separately in both cropped LZW-compressed and
cropped uncompressed forms. Place them in:

```text
co2routex/database/pipeline/spatial_cost_resistance/
```

| Country | Cropped LZW input | Cropped uncompressed input |
| --- | --- | --- |
| NL | `spatial_resistance_NL_cropped.tif` | `spatial_resistance_NL_cropped_uncompressed.tif` |
| DE | `spatial_resistance_DE_cropped.tif` | `spatial_resistance_DE_cropped_uncompressed.tif` |
| NO | `spatial_resistance_NO_cropped.tif` | `spatial_resistance_NO_cropped_uncompressed.tif` |

These benchmarks consume the supplied rasters. No raster-generation step is
required here. Confirm compression from file metadata rather than filenames
alone.

For a compression comparison, verify that the two rasters have matching pixel
values, validity masks, CRS, transform, dimensions, data type, and tiling. If
other properties differ, report those differences and do not attribute the
entire performance difference to compression.

## Select nodes and connections

The workbook must contain the node definitions and directed pipeline connections
to route. Use the same workbook and routing settings for both compression
variants of a country.

The supplied NL example is `examples/node_metrics_benchmark.xlsx`. The DE and NO
configuration examples require country-specific workbooks; check their
`workbook_path` values and provide the corresponding files. Do not use NL nodes
with another country's raster.

For a distance-scaling experiment, choose valid pairs with approximate endpoint
separations of 10, 50, 100, 150, and 200 km, where the study area permits. These
are experimental categories, not guaranteed routed distances. Record actual
endpoint separation and routed length when analysing the results.

Keep the connection set identical across comparisons. For fresh-search timing,
use:

```yaml
cache_max_routes: 0
cache_reverse_routes: false
```

Avoid including both directions unintentionally. If both are part of the
experiment, report them as distinct directed cases.

## Run a benchmark

Inspect the selected YAML before execution. Check `raster_path`, `workbook_path`,
`output_dir`, and any repetition or warm-up options supported by the current
benchmark driver. Use separate output directories for comparisons so results
remain distinguishable.

Run the general benchmark configuration:

```bash
python run_distance_benchmark.py --config config.benchmark.yaml
```

For the Netherlands compression comparison:

```bash
python run_distance_benchmark.py --config config.benchmark.NL.lzw.yaml
python run_distance_benchmark.py --config config.benchmark.NL.uncompressed.yaml
```

For Germany or Norway, use the corresponding `config.benchmark.DE.*.yaml` or
`config.benchmark.NO.*.yaml` files after supplying their country inputs.

Use the actual configuration to determine repetition and warm-up counts; do not
infer them from an older documentation example. Repetitions and their output
organisation are controlled by the benchmark driver. Record the selected counts
when presenting results.

## Read the results

Current router performance reporting uses `benchmark_results.xlsx` with the
sheets described in [README.md](README.md). For repeated runs, inspect the output
paths reported by the driver and retain all relevant run reports. Do not assume
the legacy CSV tables or `runs/run_XX` layout are produced by this version.

Use the exported field guide to identify:

| Measurement | Interpretation |
| --- | --- |
| Raster reading time | File opening, pixel and mask reading, and decoding |
| Raster preparation time | Shared validation and preparation before pair searches |
| Search time | Search work for an individual directed connection |
| Pair total time | Full processing time for that connection, including its measured components |
| Run total time | Inclusive run measurement with the scope defined below |
| Process RSS | Resident process memory, including shared raster data and native allocations |

Start by checking pair statuses, route costs, and lengths. Compare runtime only
after establishing that both runs solved equivalent routing tasks.

## Timing scope

Run totals include their measured components; pair totals likewise include
their components. Do not add inclusive totals to the component values when
calculating overall time. Shared raster loading and preparation occur at run
level, so keep them separate from per-pair search comparisons.

The router documentation defines `run_total_wall_s` as ending after routed
workbook and GIS export and memory-monitor shutdown. It excludes benchmark-report
construction/export, imports, configuration parsing, and benchmark aggregation.
Use the report's `Field_guide` when interpreting individual exported fields.

The normal launcher, `run_router.py`, additionally prints elapsed time from after
its imports through completion of `run()`, including output writes. This is a
different scope from the run-total field. Do not assume the benchmark driver
prints an identically scoped measurement without checking its output.

## Memory scope

The supplied normal-run configuration requests RSS sampling every 0.05 seconds.
Check the selected benchmark configuration for its interval. Thread scheduling
can delay samples and short peaks can be missed.

Pair RSS includes the shared raster, interpreter, native allocations, and
retained earlier results. It is not an independent allocation attributable only
to the current pair. Do not sum pair peaks to estimate total memory requirements.

Reported run peaks and report-export peaks may have different measurement
coverage. Leave practical RAM headroom rather than treating a sampled peak as
a guaranteed machine requirement.

## Repetition and operating-system caching

An application warm-up and an operating-system file cache are different things.
Disabling warm-up runs does not guarantee a cold file cache. Starting a new Python
process also does not guarantee that the operating system will reread all bytes
from storage.

For more reliable comparisons:

1. Keep input pairs, routing settings, software, and hardware consistent.
2. Run both variants under comparable server load.
3. Repeat measurements where feasible and alternate which variant runs first.
4. Report the number of observations and variability, alongside a median or mean.
5. Describe cache conditions honestly; use "first run" rather than "cold-cache
   run" unless the cache state was controlled.

Separate raster-loading differences from search-time differences. Compression
directly changes storage and decoding work; unrelated fluctuations in search
time may reflect server activity, scheduling, or memory effects.

## Retain enough information to reproduce a comparison

Keep the selected configurations, input identifiers, directed connection set,
run order, repetition settings, environment details, and performance reports.
Share raster inputs and generated results through the project's agreed data
channel. The repository retains benchmark code, configurations, and example
inputs while its ignore rules exclude local rasters and generated output folders.
