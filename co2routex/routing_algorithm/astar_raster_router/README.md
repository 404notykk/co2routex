# A* Raster Router

## Overview

`astar_raster_router` implements one routing algorithm within
[CO2RouteX](../../../README.md), a modelling project for the transport domain
of carbon capture, utilisation and storage (CCUS).

This module is specifically responsible for finding spatially least-resistant
routes for CO₂ pipelines using the A* algorithm. It is not a standalone CCUS
transport model and does not select capture sites, storage sites, transport
modes, pipeline capacities, or final network investments. Those decisions
belong to the wider CO2RouteX workflow.



## Purpose

The router:

1. reads user-defined nodes and permitted pipeline connections from an Excel
   workbook;
2. transforms the node coordinates to the coordinate reference system of the
   spatial cost-resistance raster;
3. applies A* to each permitted pipeline connection;
4. calculates the geographic length of each successful route;
5. replaces the corresponding non-zero entry in the pipeline matrix with the
   routed distance in kilometres; and
6. exports the route geometries as a GIS file for subsequent visualisation and
   analysis.


## Inputs

The router requires two inputs:

1. a GeoTIFF containing the integrated spatial cost-resistance raster; and
2. one `.xlsx` workbook containing the nodes and transport-mode matrices.


### Spatial cost-resistance raster

The GeoTIFF represents the relative spatial resistance to pipeline construction
across the study area. Raster-cell values may integrate factors such as:

- population density;
- protected areas;
- water bodies;
- terrain slope; and
- existing transport infrastructure.

The selection and representation of these spatial resistance factors are informed
by the pipeline-routing approach presented by Bogs et al. (2025).
See: https://doi.org/10.1080/24725854.2025.2602823.

These values are spatial cost-resistance factors. They are not direct monetary
costs such as EUR/km and should not be interpreted as pipeline construction
prices. The accumulated routing score is therefore a relative measure used to
compare alternative paths across the same resistance surface.

The raster must:

- have a defined coordinate reference system;
- use a projected CRS with linear units suitable for distance calculations;
- contain non-negative resistance values for traversable cells; and
- identify NoData or otherwise prohibited cells as barriers.

### Excel workbook

The workbook is normally named `node_metrics.xlsx`. A template can be generated
with:

```bash
python -m co2routex.routing_algorithm.astar_raster_router.create_input_template \
    --output node_metrics.xlsx \
    --nodes 40
```

The workbook contains four worksheets:

- `nodes`
- `pipeline`
- `truck`
- `railway`

The A* raster router reads the `nodes` and `pipeline` worksheets. The `truck`
and `railway` worksheets are retained for other components of CO2RouteX and are
not routed by this module.

#### `nodes` worksheet

Each row represents one source, sink, or intermediate node.

| Column | Description                                                               |
|---|---------------------------------------------------------------------------|
| `node_id` | Unique node identifier used in all transport-mode matrices                |
| `node_name` | Node name                                                                 |
| `longitude` | Node longitude in decimal degrees                                         |
| `latitude` | Node latitude in decimal degrees                                          |
| `altitude` | Node altitude in metres, with a default value of `10`                     |                         |
| `annual_flux` | Annual CO₂ flow associated with the emitter node                          |
| `node_type` | Node category used in subsequent CCUS full-chain optimisation, such as `cement`, `refinery`, `waste_to_energy`, `storage`, or `transport` |
| `country_code` | Country identifier associated with the node                               |

The `longitude` and `latitude` fields use EPSG:4326 coordinates. The
router transforms them to the raster CRS before converting them to raster
cells.

Requirements:

- every `node_id` must be unique;
- matrix row and column identifiers must match values in `node_id`;
- longitude and latitude must be numeric and valid; and
- no required node used by an enabled pipeline connection may have missing
  coordinates.

Note:

To support subsequent CCUS full-chain optimisation with AdOpT-NET0, emitter
nodes should preferably be classified by industrial sector, for example
`cement`, `refinery`, or `waste_to_energy`. Storage sites may be classified as
`storage`. Potential transport-switching points, including railway stations,
intermediate terminals, and known transport hubs connected to storage, may be
classified as `transport`.

#### Transport-mode matrices

The `pipeline`, `truck`, and `railway` worksheets are square directed matrices:

- row identifiers represent origin nodes;
- column identifiers represent destination nodes; and
- the cell at row `A`, column `B` represents the directed connection
  `A → B`.

Matrix values are interpreted as follows:

| Value | Meaning before routing |
|---|---|
| `0` | The connection is not permitted and will not be routed |
| Any non-zero value | The connection is permitted and should be processed |

An initial value of `1` may be used as a convenient permission flag, but the
router must not test specifically for `value == 1`. It must treat every
non-zero value as a permitted connection. This is necessary because successful
pipeline entries are subsequently replaced with routed distances in
kilometres.

After routing, the `pipeline` matrix is interpreted as:

| Value | Meaning after routing |
|---|---|
| `0` | No routed pipeline connection |
| Positive numeric value | Routed pipeline distance in kilometres |

The matrices are directed. Permitting `A → B` does not automatically permit
`B → A`. Diagonal cells represent self-connections and should normally remain
zero.

## Routing method

The resistance raster is treated as a weighted graph in which traversable
raster-cell centres are graph nodes. Depending on the configuration, movement
may be allowed between four orthogonal neighbours or all eight surrounding
neighbours.

For adjacent cells $i$ and $j$, the movement resistance is:

$$
w(i,j) = d(i,j)\frac{r_i+r_j}{2}
$$

where:

- $d(i,j)$ is the centre-to-centre movement distance;
- $r_i$ and $r_j$ are the spatial cost-resistance factors of the two cells;
  and
- $w(i,j)$ is the weighted spatial resistance of the movement.

For a square raster with cell size $s$:

- an orthogonal movement has distance $s$; and
- a diagonal movement has distance $\sqrt{2}s$.

For example, on a $100$ m raster, orthogonal movements are $100$ m and diagonal
movements are approximately $141.42$ m.

$A*$ evaluates:

$
f(n) = g(n) + h(n)
$

where $g(n)$ is the accumulated spatial resistance from the starting cell and
$h(n)$ is an admissible estimate of the remaining resistance to the
destination. With an admissible heuristic, $A*$ returns the same optimal
least-resistance route as weighted Dijkstra under the same raster graph and
movement definition, while generally exploring fewer cells.

## Setup within CO2RouteX

Any Python environment with the project
dependencies installed can run the module.

From the repository root:

```bash
python -m venv .venv
```

Activate the environment using the command appropriate to the operating system,
then install CO2RouteX and its dependencies:

```bash
python -m pip install -e .
```

The parent directories and this directory should contain `__init__.py` files so
that the router can be imported as part of the `co2routex` package. Imports
inside the router should be package-relative or use the complete
`co2routex.routing_algorithm.astar_raster_router` package path.

## Configuration

Copy `config.example.yaml` to a working configuration file and set at least:

- the spatial cost-resistance GeoTIFF path;
- the input `.xlsx` workbook path;
- the output directory;
- the movement connectivity (e.g. `4` or `8`);
- NoData and barrier handling;
- the maximum node-snapping distance; and
- the output GIS filename.

Paths may be absolute or relative to the repository root. Repository-relative
paths are recommended for reproducible project runs.

## Running the router

Run the module from the CO2RouteX repository root:

```bash
python -m co2routex.routing_algorithm.astar_raster_router.run_router \
    --config co2routex/routing_algorithm/astar_raster_router/config.yaml
```

Running the module in this way ensures that package imports are resolved
consistently, regardless of the IDE or terminal used.

## Outputs

The router produces two principal outputs.

### Updated Excel workbook

```text
node_metrics_routed.xlsx
```

This is a copy of the input workbook in which each successfully routed non-zero
entry in the `pipeline` matrix is replaced with the corresponding route length
in kilometres. The `nodes`, `truck`, and `railway` worksheets are preserved.

The input workbook should not be overwritten. Writing a separate routed
workbook preserves the original connection permissions and supports
reproducibility.

### GIS route file

```text
routes.gpkg
```

The GeoPackage contains the routed pipeline geometries in the raster CRS. Each
route feature should include, at minimum:

- origin node ID;
- destination node ID;
- route length in kilometres; and
- accumulated spatial resistance score.

The GeoPackage can be read directly with common Python GIS libraries and
visualised in Python or desktop GIS software.

## Assumptions and limitations

- The calculated route is a planning-level least-resistance path, not a
  construction-ready engineering alignment.
- Route geometry is constrained by the raster resolution and movement
  connectivity.
- Finer computational subdivisions do not add new thematic information when
  they inherit resistance values from a coarser raster.
- Nodes outside the raster extent cannot be routed.
- Nodes on barriers may be snapped only within the configured maximum distance.
- A route cannot cross disconnected barrier regions unless a traversable path
  exists.
- Spatial resistance scores are meaningful for comparing routes produced from
  the same resistance model; they are not monetary pipeline costs.
