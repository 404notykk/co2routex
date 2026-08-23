# Truck Network Router

Standalone truck routing for CO2RouteX. The router uses country-specific OSM
road-network GeoPackages, embeds truck connection eligibility, snaps facilities
to nearby road segments, calculates directed shortest paths, and writes route
distances in kilometres to the `truck` worksheet.

## Position in CO2RouteX

```text
co2routex/
└── routing_algorithm/
    ├── connection_algorithm/
    ├── astar_raster_router/
    ├── truck_network_router/
    └── railway_network_router/
```

This folder runs independently. It does not call the pipeline connection
algorithm and does not modify the `pipeline` or `railway` metrics.

## Required workbook structure

The `nodes` sheet must contain:

```text
node_id
node_name
longitude
latitude
altitude
annual_flux
node_type
country_code
```

`node_type=storage` is treated as storage, `node_type=transport` as a transport
node, and every other non-empty type (for example `cement`) as an emitter.

The router recreates the square `truck` sheet using the node order in `nodes`:

- `0` means prohibited, unavailable, or not routed;
- a positive value is the successful truck distance in kilometres;
- row is `from_id` and column is `to_id`.

## Required road networks

Each configured GeoPackage must contain a line layer, normally named
`truck_network`. The current preparation workflow extracts:

- `motorway` and `motorway_link`;
- `trunk` and `trunk_link`;
- `primary` and `primary_link`.

Keep `highway`, `oneway`, and preferably `access`/`motor_vehicle` tags in the
GeoPackage. If `oneway` is inside GDAL's `other_tags` field, this router parses
it. Motorways and motorway links are treated as one-way when no explicit
`oneway` value is present.

## Installation

From this folder:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate with:

```powershell
.venv\Scripts\activate
```

## Configuration

Copy the example and edit the paths:

```bash
cp config.example.yaml config.yaml
```

All relative paths are resolved from the YAML file, not from the terminal's
current working directory.

By default, `output_workbook_path: null` updates `workbook_path` itself. Set a
different output path to preserve the input workbook.

### Connection policies

`baseline` generates all distinct ordered pairs within the same country.

`directed_chain` applies:

```text
emitter   -> emitter, transport, storage
transport -> transport, storage
storage   -> none
```

Set `candidate_source: existing_nonzero` to route only existing non-zero cells
in the `truck` sheet while still checking same-country direction and network
availability.

## Run

```bash
python run_truck_router.py config.yaml
```

For detailed logs:

```bash
python run_truck_router.py config.yaml --verbose
```

## Distance definition

The network path is measured in the configured metric `working_crs`. By
default, the value placed in `truck` is:

```text
origin-to-network snap distance
+ routed road-network distance
+ network-to-destination snap distance
```

The two snap sections approximate unrepresented first-/last-mile industrial
access. Set `include_snap_distance_in_metric: false` to store only the main
network distance. Connections exceeding `maximum_snap_distance_m` receive `0`.

## Outputs

- Updated `truck` worksheet in the configured workbook.
- `output/truck_routes.gpkg`, layer `truck_routes`, for successful routes.
- `output/truck_route_summary.csv` for successful and failed attempts.

The route GeoPackage includes the straight access connectors so it can be
checked visually in ArcGIS Pro.

## Manual test

```bash
python create_test_data.py
python run_truck_router.py config.test.yaml --verbose
```

This creates a small artificial road network and a separate routed test
workbook under ignored test directories.

## Automated tests

```bash
python -m pytest -q
```

## Current scope

- Routes use Dijkstra's shortest-distance algorithm on the extracted OSM graph.
- Country-specific files currently support same-country pairs.
- OSM turn-restriction relations are not processed.
- Access restrictions are retained for future filtering but are not yet applied.
- GeoPackage conversion loses original OSM node identities. Topology is rebuilt
  from line vertices, so the output should be visually validated against OSM.
