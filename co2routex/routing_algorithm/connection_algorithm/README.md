# CO2 Connection Algorithm

This package generates directed candidate node pairs before spatial routing.
It deliberately separates three decisions:

1. **Connection generation** identifies plausible directed pairs.
2. **A\* raster routing** calculates the least-resistant spatial route for
   each candidate pair.
3. **AdOpT-NET0/MILP optimisation** selects routes and determines CO2 flows.

The connection generator does not use annual flow, storage capacity, route
capacity, or any other optimisation decision variable.

## Placement

Place this folder beside `astar_raster_router`:

```text
routing_algorithm/
├── astar_raster_router/
└── connection_algorithm/
```

## Node types and direction rules

The default emitter types are:

- `cement`
- `refinery`
- `waste_to_energy`

Terminal destination types are:

- `storage`
- `utilisation`

Potential switching points use:

- `transport`

Default directed rules:

| Origin | Destination | Allowed |
|---|---|---:|
| Emitter | Storage or utilisation | Yes |
| Emitter | Transport | Yes |
| Transport | Storage or utilisation | Yes |
| Transport | Transport | Configurable |
| Emitter | Emitter | No by default; optional directional rule |
| Storage or utilisation | Any node | No |

Node types are matched case-insensitively. Unknown types cause a validation
error instead of being silently ignored. Edit the YAML lists if another
industrial emitter classification is needed.

## Optional emitter-to-emitter direction rule

The baseline remains:

```yaml
emitter_to_emitter_rule: "disabled"
```

With this setting, no emitter-to-emitter candidates are generated.

To activate the optional heuristic:

```yaml
emitter_to_emitter_rule: "toward_nearest_destination"
emitter_to_emitter_max_detour_factor: 1.5
```

For every unordered pair of emitters A and B, the algorithm calculates each
emitter's distance to its own nearest `storage` or `utilisation` node. The
emitter farther from a destination may connect to the closer emitter. If A is
farther than B, `A → B` is retained only when:

```text
distance(A, B) + nearest_destination_distance(B)
    <= max_detour_factor × nearest_destination_distance(A)
```

The reverse `B → A` is not generated. Thus, the rule creates at most one
direction for each emitter pair. If both emitters are equally distant from
their nearest destinations, neither direction is generated.

This is only a geometric candidate-screening heuristic. It does not allocate
flow, compare emitter capacity, or decide pipeline investment. Those decisions
remain in the MILP stage.

## Workbook input

The workbook must contain a `nodes` worksheet. Its required columns are:

| Column | Meaning |
|---|---|
| `node_id` | Unique node identifier |
| `longitude` | X coordinate in `nodes_crs` |
| `latitude` | Y coordinate in `nodes_crs` |
| `node_type` | Emitter, storage, utilisation, or transport classification |

`node_name` and all other columns are preserved. Although the coordinate
columns retain the established names `longitude` and `latitude`, they may hold
projected X/Y values when `nodes_crs` is a projected CRS.

## Generation methods

### `all_eligible`

Retains every directed pair permitted by the node-type rules. This is the
recommended research baseline because it avoids excluding alternatives before
MILP optimisation.

### `nearest_k_with_relative_threshold`

First ranks eligible destinations for each origin by geodesic distance. It
then retains:

- the nearest `k` eligible destinations; and
- every additional destination no farther than
  `relative_distance_factor × nearest distance`.

An optional `max_distance_km` is applied before ranking. This method is a
computational screening option, not a flow-allocation model.

The effective number retained may exceed `k`. For example, if `k` is 3 but
five destinations are within 1.5 times the nearest distance, all five are
retained.

## Configuration

Copy `config.example.yaml` to `config.yaml` and update the workbook path.
Relative paths are resolved from the directory containing the YAML file.

Research baseline:

```yaml
method: "all_eligible"
```

Optional screening:

```yaml
method: "nearest_k_with_relative_threshold"
k: 3
relative_distance_factor: 1.5
max_distance_km: null
```

The value of `k` should later be justified through sensitivity testing, for
example `k = 1, 3, 5, 10, all`, comparing routing workload and the downstream
MILP solution.

## Running

From the directory containing `connection_algorithm`:

```bash
python -m connection_algorithm --config connection_algorithm/config.yaml
```

The convenience script may also be used:

```bash
python connection_algorithm/run_connection_generator.py \
  --config connection_algorithm/config.yaml
```

## Workbook update

The configured `workbook_path` is updated in place. No separate output workbook
or candidate CSV is created. Each configured mode worksheet is created or
replaced by a square directed matrix:

- `1` means the pair is a routing candidate;
- `0` means the pair is not a candidate;
- every diagonal value is `0`.

The same candidate matrix is written for every configured mode because
mode-specific infrastructure eligibility is not yet encoded in the node data.
The workbook update is atomic: a temporary copy is edited and validated before
it replaces the original file. Nevertheless, every pre-existing configured
mode sheet, such as `pipeline`, is intentionally replaced.

Pass the same workbook to `astar_raster_router`. The router calculates routes
only for positive matrix entries and writes routed distances according to its
own output configuration.

Users may skip this package completely and provide their own pre-defined
connection matrices to the A* router.

## Tests

Run:

```bash
pytest connection_algorithm/tests
```

The tests cover direction rules, the two-node case, all-eligible generation,
nearest-k screening, transport-node behaviour, and in-place workbook updates.
