# CO2RouteX container-based transport cost model — v0.3.0

Calculate truck and railway transport coefficients and annual costs from route
tables (XLSX, CSV, Parquet or GeoParquet) or the original square distance matrices.
The package retains the equations in the supplied screenshot and exports costs in
both EUR 2021 and EUR 2024. Existing *_eur_* monetary fields remain EUR 2021;
additional *_eur2024_* fields contain the converted values.

## Cost equations and units

| Mode | UC, EUR2021/(tCO2 km) | v = UC*d, EUR2021/tCO2 |
|---|---|---|
| Truck | 5.58/d + 0.15 | 5.58 + 0.15*d |
| Railway | 28.9/d + 0.07 | 28.9 + 0.07*d |

For a directed connection, with physical routed distance d in km:

~~~text
F = 0                                   EUR2021/year
C = F*y + v*Q = v*Q                      EUR2021/year
average_cost = C/Q = v                   EUR2021/tCO2, for Q > 0
~~~

Q is annual **transported** CO2 in tCO2/year. No capture fraction is applied.
The constants 5.58 and 28.9 are distance-independent **per-tonne charges**;
they are not annual fixed costs. F=0 means no separately modelled annual fixed
charge, not that real vehicles have no capital costs.

Distance is already embedded in v. Do not multiply by distance again,
apply another capital recovery factor, or insert v as an AdOpT-NET0 network CAPEX
coefficient in EUR/(t/h). Use it as an annual-flow transport cost coefficient.
For timestep flows, the downstream model must apply the corresponding time weights.
Connection availability and any actual capacity limits remain separate constraints.

The reference flow does not size a fleet or imply a maximum transport capacity.
This model assumes a constant per-tonne charge at a given distance; it does not
represent flow-dependent economies of scale.

## Install and run

Python 3.10+:

~~~bash
python -m pip install -r requirements.txt
python run_transport_cost_model.py --config config.yaml
~~~

The bundled config.yaml runs four illustrative CSV scenarios. It is not a
production dataset. All YAML paths resolve relative to that YAML file.

For a workbook route table, copy/edit config.workbook.example.yaml. For Parquet,
copy/edit config.parquet.example.yaml and match the actual column names:

~~~yaml
input_path: /path/to/routes.parquet
from_id_column: emitter_name
to_id_column: sink_name
distance_column: distance_km
annual_flow_column: co2_t_per_yr
mode_column: mode
~~~

Run both modes when a mode column identifies each route:

~~~bash
python run_transport_cost_model.py --config config.parquet.example.yaml
~~~

Or select one mode, including files that contain only that mode and have no mode column:

~~~bash
python run_truck_cost_model.py --config config.parquet.example.yaml
python run_railway_cost_model.py --config config.parquet.example.yaml
~~~

The equivalent common-launcher flag is --transport-mode truck or
--transport-mode railway. road is an alias for truck; rail and train are aliases
for railway. These aliases apply both to input mode values and CLI mode selection.
The original mode column is preserved in the costed input copy.
If a mode column is present, it is respected even when a single-mode launcher is used.
If it is absent, select one mode or set default_transport_mode explicitly.
No mode is inferred from a filename.

Optional one-row run:

~~~bash
python run_transport_cost_model.py --config config.parquet.example.yaml --row 1
~~~

--row is the **1-based stored data-row position**, before mode/status filters,
excluding an XLSX/CSV header. A saved pandas index does not change this numbering.
A selected row excluded by filters raises an error; another row is never substituted.
This option applies only to route tables, not square matrices.

--input PATH, --output-dir PATH, --annual-flow-t-per-year Q and
--require-annual-flow override the corresponding configuration for that run.
CLI paths resolve relative to the working directory.

## Route-table inputs

The default XLSX sheet is transport_route_metrics. Set route_metrics_sheet
to the actual worksheet name. Headers are in row 1 and must be unique.
The required attributes are origin ID, destination ID and distance, using the
configured names. Duplicate node pairs are allowed and remain separate rows.

Distances must be finite and strictly positive for table rows.
Rows with mode pipeline, ship, shipping or barge are excluded. Other unrecognised
mode labels raise an input error. If the configured status column exists,
only accepted_statuses are processed (default: [ok], case-insensitive).
Set status_column: null to disable this filter.

Annual-flow precedence:

1. The configured annual_flow_column in the row.
2. Explicit annual_flow_t_per_year in YAML.
3. Otherwise, coefficients only: annual and average cost fields remain blank.

A supplied zero Q overrides the fallback, gives annual cost zero, and leaves average
cost blank because C/Q is undefined. Negative, nonnumeric and infinite flows fail the
route. require_annual_flow: true also fails routes without an annual flow.
There is no silent use of a node's emissions as its route flow.

Missing IDs, invalid distances, invalid flows or nonfinite calculated costs are recorded
as route failures. Source format, header, mode and configuration errors stop the run.
XLSX input formulas need cached values saved by Excel/LibreOffice; this package does
not evaluate input formulas.

## Original matrix workbook

Use config.matrix.example.yaml. Old workbook_path, truck_sheet, railway_sheet
and output filename settings remain supported.

Each mode sheet contains matching origin/destination node IDs in the same order:
destinations in row 1, origins in column A. Distances are directed and in km;
zero or blank means unavailable. Invalid nonzero cells are recorded as failures.

To calculate annual costs, supply matching flow matrices, for example:

~~~yaml
truck_annual_flow_sheet: truck_annual_flow
railway_annual_flow_sheet: railway_annual_flow
~~~

Each flow matrix must have exactly the same node IDs/order as its distance
matrix, with values in tCO2/year. An explicit global annual_flow_t_per_year
can fill missing flow cells or provide one scenario flow for all connections.
The nodes worksheet is never used to infer flow.

Matrix copies retain the original sheets and add, for each selected mode:

- MODE_gamma2: v in EUR2021/t, retained for compatibility.
- MODE_fixed_cost: F in EUR2021/year.
- MODE_annual_cost: C in EUR2021/year, if flow is available.
- MODE_gamma2_eur2024: v in EUR2024/t.
- MODE_fixed_cost_eur2024: F in EUR2024/year.
- MODE_annual_cost_eur2024: C in EUR2024/year.
- MODE_reference_flow: Q in tCO2/year.
- MODE_cost_status: ok, failed or unavailable.
- container_cost_coefficients: long-form summary including errors and missing-flow notes.
- container_cost_model_info: settings and conventions.

Both gamma2 matrices retain the old zero sentinel for unavailable connections.
Failed coefficients are blank. **Always use cost_status to distinguish availability**;
a zero coefficient is not permission to transport on an unavailable route.
Other numeric matrices use blanks for unavailable/failed connections.
Previously generated matrices are refreshed, including removal of stale matrices
for modes excluded from the current run. Source distance and flow matrices are retained.

## Outputs

By default, each run writes:

1. container_transport_cost_results.xlsx: a report workbook.
2. INPUT_costed.EXT: a costed input copy in its original format.

Single-mode runs use truck_cost_results.xlsx / railway_cost_results.xlsx and
INPUT_truck_costed.EXT / INPUT_railway_costed.EXT. Matrix workbook names retain
the legacy configurable names. output_workbook_name can override the report name.
write_xlsx and write_costed_input independently control these outputs.

Report sheets:

| Sheet | Purpose |
|---|---|
| summary | Every attempted route, direct annual totals, coefficients, status and errors |
| coefficients | Successful routes' F and v for use in annual-flow optimisation |
| calculation_components | The two additive regression terms; not a CAPEX/OPEX breakdown |
| settings | Parameters, source note, units convention and success/failure counts |
| units | Field definitions and units |
| read_me | Interpretation and accounting rules |

Route-table copies receive these current-run columns:

| Column | Unit / meaning |
|---|---|
| container_transport_mode | truck or railway |
| container_coefficient_method | distance_regression |
| container_reference_flow_t_per_year | tCO2/year; blank if missing |
| container_fixed_cost_eur_per_year | EUR2021/year; zero for successful calculations |
| container_flow_cost_eur_per_t | EUR2021/tCO2 |
| container_annual_cost_eur_per_year | EUR2021/year; blank if missing flow or failed |
| container_average_cost_eur_per_t | EUR2021/tCO2; blank at zero/missing flow or failed |
| container_cost_status | ok or failed; blank for unprocessed rows |
| container_cost_error | Error type and explanation |
| container_cost_note | Coefficients-only or zero-flow explanation |
| container_fixed_cost_eur2024_per_year | EUR2024/year; zero on successful routes |
| container_flow_cost_eur2024_per_t | EUR2024/tCO2 |
| container_annual_cost_eur2024_per_year | EUR2024/year |
| container_average_cost_eur2024_per_t | EUR2024/tCO2 |
| container_cost_reference_year | 2021; original *_eur_* field basis |
| container_converted_price_year | 2024; added *_eur2024_* field basis |
| container_eur2021_to_eur2024_factor | Actual applied multiplier |
| container_currency_conversion_basis | Factor source and price-index coverage |

The original file is never overwritten. All original rows/columns are retained
in their original order. Existing container result fields are refreshed and cleared
for unprocessed/failed rows so stale costs cannot appear current.
Only selected/eligible routes appear in the separate report.

XLSX copies preserve original sheets, formulas and formatting; result columns are
appended to the selected sheet and full-input Excel tables are expanded.
Parquet/GeoParquet copies stream original data, preserving geometry bytes, nested
columns, original field metadata, GeoParquet metadata and saved pandas indexes.
Costing reads only relevant attributes; --row reads its containing Parquet row group.
CSV copies preserve identifier strings such as leading zeros.

Route failures do not stop other calculations. Even an all-failed run writes the
report and costed copy. The CLI exits successfully when output writing succeeds,
including runs with recorded failures: inspect the logged counts and exported statuses.
There is no checkpoint/resume facility. Outputs are static calculations; rerun after
changing inputs.

## Source, monetary year and scope

Source attribution: Oeuvray et al. (2024), *Multi-criteria assessment of inland and
offshore carbon dioxide transport options*, Journal of Cleaner Production,
https://doi.org/10.1016/j.jclepro.2024.140781. The equations match the supplied
container-based truck/train screenshot and are also reproduced in Table 1 of
Hörbe Emanuelsson et al. (2025), https://doi.org/10.1016/j.ijggc.2025.104442.

The paper's techno-economic data use a 2021 price basis (section 3.2); the
publication year 2024 is not the monetary year. Regression parameters must therefore
be supplied in EUR 2021. cost_reference_year defaults to 2021 and other years are
rejected. A legacy YAML value of null is migrated to 2021 when loading the file.

### Additional EUR 2024 outputs

Every monetary result uses the same conversion:

~~~text
k = mean(PPI in 2024) / mean(PPI in 2021)
v_2024 = k * v_2021
F_2024 = k * F_2021 = 0
C_2024 = k * C_2021 = v_2024 * Q
~~~

The default matches AdOpT-NET0 **v0.1.10**'s `correct_inflation` / `convert_currency`
method and its bundled Eurostat data. The package includes the relevant monthly
values in `container_transport_cost_model/currency.py`; no AdOpT installation or
network connection is required. It uses Eurostat STS_INPP_M, euro area 20, total
industry excluding construction, sewerage, waste management and remediation,
unadjusted producer prices, index 2021=100.

**Coverage limitation:** that snapshot is dated 2025-01-21 and contains all 12
months of 2021 but only January-November 2024. Its averages are 99.99166666666667
and 120.88181818181818, respectively, giving **k = 1.2089189250619368**.
These reproduce AdOpT's snapshot, not a complete calendar-year 2024 average.
The conversion basis and month coverage are recorded in the report settings,
Parquet schema metadata, and the costed table's conversion-basis column.
This is an industrial producer-price adjustment for model consistency, not a
new estimate of 2024 truck/rail tariffs. It applies neither exchange rates nor
discounting and does not change distance, mass flow or availability.

Source implementation and index snapshot:
- https://github.com/UU-ER/AdOpT-NET0/blob/v0.1.10/adopt_net0/database/utilities.py
- https://github.com/UU-ER/AdOpT-NET0/blob/v0.1.10/adopt_net0/database/data/producer_price_index_euro.csv
- https://ec.europa.eu/eurostat/databrowser/view/sts_inpp_m/default/table?lang=en

The default configuration is:

~~~yaml
cost_reference_year: 2021
eur2021_to_eur2024_factor: null
eur2024_factor_source: null
~~~

For a different verified index snapshot (for example, a full 12-month 2024
average), set eur2021_to_eur2024_factor to its numeric ratio and document the
series, coverage and source in eur2024_factor_source. Both override fields are
required together; a factor must be finite and greater than zero. No additional
conversion is applied to the original EUR 2021 results.

For truck transport over 100 km and Q=100,000 t/year, the original v is
20.58 EUR2021/t and C is 2,058,000 EUR2021/year. With the default snapshot,
the added values are approximately 24.879551 EUR2024/t and
2,487,955.15 EUR2024/year. Calculations use full precision; formatting only
controls display. Missing/failed values stay blank, and zero annual flow still
produces a zero annual cost with an undefined (blank) average cost.

When updating from an earlier ZIP, replace the Python package and launchers,
keep your edited input paths/column mappings in YAML, and rerun the same command.
The new conversion settings are optional because the defaults above apply.
All supported route-table formats and matrix workbooks receive the added costs.

This package adds no separate vehicle, wagon, terminal, handling, empty-return,
access-leg, compression, liquefaction, energy or maintenance charges.
It does not independently identify which of those are embedded in the regressions.
Confirm scope before adding costs or comparing complete transport chains.
Treat each row as one intended transport leg: avoid repeatedly charging the
distance-independent term at arbitrary routing nodes, and keep rail-only distances
separate from any truck access legs when assembling multimodal chains.

## Python API and checks

~~~python
from container_transport_cost_model import (
    TransportCostConfig, RouteInput, calculate_route, run_with_outputs,
)

result = calculate_route(
    RouteInput("truck", "A", "B", distance_km=100, annual_flow_t_per_year=100_000),
    TransportCostConfig(),
)
assert abs(result.annual_cost_eur_per_year - 2_058_000) < 1e-6
v = result.coefficient.gamma2_eur_per_t

config = TransportCostConfig.from_yaml("config.yaml")
paths = run_with_outputs(config)  # report and costed_input paths
~~~

The existing run(config, modes) function still returns the costed-input path,
or the report path if costed-input writing is disabled. The unit_cost,
calculate_gamma2 and calculate_pair_coefficient helpers remain available.

~~~bash
python -m pip install -e ".[test]"
python -m pytest -q
~~~

Checks cover source equations, annual accounting, all input layouts, missing/zero/invalid
flows, route failures, physical row selection, status/mode filters, preservation of
source data/metadata/indexes, stale-result clearing, output collision protection,
EUR 2024 conversion/coverage, and documented factor overrides.
