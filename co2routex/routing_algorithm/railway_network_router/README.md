# Railway network router

The railway router has two independent missions. Both can be executed by
opening the corresponding Python file and clicking Run.

## Database structure

Country files are stored directly in one flat folder:

```text
database/container_based_train/
├── railway_NL.gpkg
├── railway_stations_NL.gpkg
├── station_routes_NL.gpkg
├── station_distances_NL.csv
├── railway_NO.gpkg
├── railway_stations_NO.gpkg
├── station_routes_NO.gpkg
├── station_distances_NO.csv
├── railway_DE.gpkg
├── railway_stations_DE.gpkg
├── station_routes_DE.gpkg
└── station_distances_DE.csv
```

The router does not download or extract OSM data. The country railway
GeoPackages and pre-identified station GeoPackages must already exist.

## Mission 1: calculate station distances

Run:

```text
calculate_station_distances.py
```

It reads:

- `railway_<COUNTRY>.gpkg`
- `railway_stations_<COUNTRY>.gpkg`

It then:

1. projects the railway and station layers to the configured routing CRS;
2. snaps every pre-identified station to the railway network;
3. calculates the shortest railway route between every connected station pair;
4. writes `station_routes_<COUNTRY>.gpkg`;
5. writes `station_distances_<COUNTRY>.csv`.

The station input must contain unique `station_id` and `station_name` fields,
or the field names must be changed in `config.yaml`.

If an input GeoPackage contains multiple layers, add its layer name to the
relevant configuration section:

```yaml
railway_layer: railway_network
stations_layer: railway_stations
```

When a layer setting is omitted, the first layer in the GeoPackage is read.

## Mission 2: process a railway request

Run:

```text
process_railway_request.py
```

When `railway_request.requested` is `true`, it:

1. finds the nearest pre-identified station within 5 km of each original node;
2. ignores nodes without a station within 5 km;
3. deduplicates stations selected by multiple nodes;
4. adds selected stations to the `nodes` sheet with `node_type = transport`,
   `annual_flux = 0`, and `altitude = 10`;
5. expands the `pipeline`, `truck`, and `railway` matrices;
6. leaves the railway matrix all zero when fewer than two stations are involved;
7. otherwise inserts the precalculated station-to-station distances;
8. saves the configured workbook in place.

This mission must run before the connection algorithm generates pipeline and
truck connections, ensuring the newly introduced transport nodes are included.

When `requested` is `false`, the workbook is not changed.

## Workbook requirements

The workbook must contain these sheets:

```text
nodes
pipeline
truck
railway
```

The `nodes` sheet must contain:

```text
node_id
node_name
longitude
latitude
altitude
annual_flux
node_type
```

The transport-mode sheets must be square matrices with node IDs in the first
row and first column.

## Configuration

All paths in `config.yaml` are resolved relative to the location of that YAML
file. Change `country_code` and the country-coded filenames when processing a
different country.

The default structure assumes the router is located at:

```text
co2routex/routing_algorithm/railway_network_router/
```

and the database is located at:

```text
co2routex/database/container_based_train/
```

## Installation and tests

Install the dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the included tests:

```bash
python -m unittest discover -s tests -v
```

