# Pipeline transport cost model — version 0.5.0

Calculate CO2 pipeline costs from **physical routed distance** and **annual transported CO2**.
The package retains the Oeuvray engineering cost model and the EUR 2021 data.
Exports also include EUR 2024 monetary columns using the same PPI conversion as
the updated container transport package. The existing EUR 2021 values remain unchanged.

Version 0.4 calculates **baseline costs only**. Resistance influences routing upstream; it does
not multiply CAPEX, fixed OPEX, electricity costs or the distance in this package. The name
baseline remains in the results so additional cost bases can be introduced explicitly later.
This matches the resistance treatment in the released Bogs et al. implementation, not its
separate engineering/economic model:
https://zenodo.org/records/15829448

## Quick start

Install Python 3.10+ dependencies and run the illustrative cases:

~~~bash
python -m pip install -r requirements.txt
python run_pipeline_cost_model.py --config config.yaml
~~~

The supplied YAML uses:

~~~yaml
mode: both
coefficient_method: fixed_design
output_dir: outputs/pipeline_costs
output_workbook_name: pipeline_cost_results.xlsx
write_xlsx: true
write_costed_input: true
write_csv: false
write_json: false
~~~

By default there are **two output files**:

1. **pipeline_cost_results.xlsx**: the report, with separate sheets serving separate purposes.
2. **<input_name>_costed.<input_extension>**: a copy of the input with the original nine
   result fields and four additional EUR 2024 cost fields (13 result fields in total).

Examples: routes_NL.parquet becomes routes_NL_costed.parquet;
node_metrics.xlsx becomes node_metrics_costed.xlsx. CSV and .geoparquet inputs retain
their respective formats too. The original input is never overwritten.

The example CSV contains illustrative scenarios, not the SINTEF benchmark dataset.
No historical result workbooks are bundled with this release.

## Version 0.5.0: EUR 2024 alongside EUR 2021

All exports from run_pipeline_cost_model.py now contain both price-year representations.
No extra output files or cost bases are created. The conversion happens after engineering
calculations, preserving the selected design, existing cost values and route-failure handling.
There are no new dependencies and existing YAML files work without adding new settings.

The default conversion follows AdOpT-NET0's ratio of available monthly Eurostat producer
price indices (PPI), using the same observations as the updated truck/rail package:

~~~text
PPI_2021 = mean(January–December 2021) = 99.99166666666667
PPI_2024 = mean(January–November 2024) = 120.88181818181819
k = PPI_2024 / PPI_2021 = 1.208918925061937
F_2024 = k * F_2021                         [EUR 2024/year]
v_2024 = k * v_2021                         [EUR 2024/tCO2]
C_2024(Q) = F_2024 * built + v_2024 * Q     [EUR 2024/year]
~~~

The bundled observations come from AdOpT-NET0 0.1.10's Eurostat snapshot dated 2025-01-21:
https://github.com/UU-ER/AdOpT-NET0/blob/v0.1.10/adopt_net0/database/data/producer_price_index_euro.csv
Series: sts_inpp_m, geo=EA20, nace_r2=B-E36, indic_bt=PRC_PRR, s_adj=NSA, unit=I21, freq=M.
**December 2024 is absent**, so the default uses 11 target-year months, not a complete
2024 annual index. The observations are embedded in pipeline_cost_model/currency.py;
no online data download is needed when running the model.

Every monetary total and component receives the same factor, including upfront CAPEX,
annualised CAPEX, fixed OPEX, electricity OPEX, F, v and average total cost. Annualisation
rates, lifetimes, flow, distance, physical design and percentage fit errors are unchanged.
Price-year conversion is separate from annualisation and discounting. Never add the EUR
2021 and EUR 2024 versions of the same cost together.

The electricity input is explicitly assumed to be **EUR 2021/MWh**. The default 60 is a
scenario assumption; this package does not establish it as an observed 2021 market price.
Its EUR 2024 equivalent is approximately 72.535136/MWh under the default factor, not an
observed 2024 electricity tariff. Enter electricity prices on the 2021 price basis;
electricity_price_reference_year accepts only 2021 to prevent silently mixing price years.

Optional YAML settings (defaults shown):

~~~yaml
cost_reference_year: 2021
electricity_price_reference_year: 2021
eur2021_to_eur2024_factor: null
eur2024_factor_source: null
~~~

Null uses the bundled factor. To use another series or a complete-year index, supply a
finite positive factor and a nonblank source description in eur2024_factor_source. Use the
same factor in the pipeline and container packages for comparable results. Do not change
cost_reference_year to 2024: it describes the underlying cost data, not the added columns.
The report settings and run log show the resolved factor and source; default settings also
record the index means, month counts, snapshot date and coverage.

Report changes:

- summary: EUR 2024 copies of direct investment, annual cost totals and average cost,
  plus any monetary reconciliation or fitted-reference fields.
- coefficients: fixed_coefficient_eur2024_per_year and flow_coefficient_eur2024_per_t
  on all contribution and total rows.
- cost_components: value_eur2024 and unit_eur2024 alongside the existing value and unit.
- design: electricity_price_eur2024_per_mwh alongside the original input assumption;
  physical and energy quantities retain their original values.
- fit_samples / fit_diagnostics, when active: converted monetary predictions, actual
  costs and RMSE; percentage errors are unchanged.
- units and read_me: definitions of both price-year representations.

Failed, excluded and unprocessed routes retain blank current-run monetary fields in both
price years. Benchmark mode still leaves F/v blank; flow_range approximation-only mode
still leaves direct reference annual/average costs blank.

To update your existing server installation, replace pipeline_cost_model/ and the two
run_pipeline_*.py launchers with the copies in this ZIP; also replace pyproject.toml if
you installed the package. Keep your configured YAML files, input files and output paths.
From the pipeline directory, test with your existing configuration:

~~~bash
python -u run_pipeline_cost_model.py \
  --config config.parquet.example.yaml \
  --mode approximation \
  --coefficient-method fixed_design \
  --row 1
~~~

Omit --row and set input_row: null to run the entire eligible input. The same output
filenames are used, so rerunning into the same output directory replaces those outputs.

## Parquet: run the first stored row

Edit config.parquet.example.yaml. Set input_path to your actual routes_NL.parquet path.
Relative paths are resolved against the YAML file's directory.

~~~yaml
input_path: /absolute/path/to/database/source_sink/routes_NL.parquet
from_id_column: emitter_name
to_id_column: sink_name
distance_column: distance_km
annual_flow_column: co2_t_per_yr
mode: both
coefficient_method: fixed_design
~~~

Use the exact flow column in your file. The screenshots showed co2_t_per_yr; if yours
is co2_per_yr, change annual_flow_column accordingly. Stable emitter_id/sink_id fields
can be mapped instead of names. Flow must be transported/captured CO2 in tonnes/year.
No additional capture fraction is applied.

~~~bash
python run_pipeline_cost_model.py --config config.parquet.example.yaml --row 1
~~~

--row is **1-based physical data-row position**, before status/mode filters and independent
of a saved pandas index. It excludes an Excel/CSV header. The selected row must have
status=ok and mode=pipeline if those columns exist. Omitting --row processes all eligible rows.

Only selected rows are attempted. The costed input copy always retains **all original rows**
in their original order. Its 13 result columns are blank for unprocessed rows. A new run
refreshes these columns rather than carrying stale costs or errors from another run.

Parquet costing projects relevant attributes and can read one selected row group. Writing
the full costed copy streams the entire original file in batches. Original geometry, nested
columns, Arrow field metadata, GeoParquet metadata and pandas indexes are preserved.
CSV input values, including identifiers with leading zeros, are retained in the CSV copy.
Excel copies retain the original worksheets, formulas and formatting, with the 13
result columns appended to the configured route sheet.

Existing input columns are retained. If an input already contains older model-result
columns, the 13 pipeline_* fields below identify this release's current-run results.

## The 13 result columns

| Column | Meaning | Unit |
|---|---|---|
| pipeline_cost_basis | baseline for calculated rows | text |
| pipeline_coefficient_method | fixed_design or flow_range when coefficients were requested | text |
| pipeline_reference_flow_t_per_year | Flow of the direct reference calculation | tCO2/year |
| pipeline_fixed_cost_eur_per_year | Total annual fixed coefficient F | EUR 2021/year |
| pipeline_flow_cost_eur_per_t | Total flow coefficient v | EUR 2021/tCO2 |
| pipeline_annual_cost_eur_per_year | Direct total annual cost at the reference flow | EUR 2021/year |
| pipeline_average_cost_eur_per_t | Direct total annual cost divided by reference flow | EUR 2021/tCO2 |
| pipeline_cost_status | ok for a successful calculation; failed for an unsuccessful calculation | text |
| pipeline_cost_error | Exception type and message for a failed calculation; blank for success | text |
| pipeline_fixed_cost_eur2024_per_year | Annual fixed coefficient F in 2024 prices | EUR 2024/year |
| pipeline_flow_cost_eur2024_per_t | Flow coefficient v in 2024 prices | EUR 2024/tCO2 |
| pipeline_annual_cost_eur2024_per_year | Direct total annual cost in 2024 prices | EUR 2024/year |
| pipeline_average_cost_eur2024_per_t | Direct annual cost divided by reference flow in 2024 prices | EUR 2024/tCO2 |

For fixed_design, F + v*Q0 equals the direct annual cost. For flow_range, the fitted
coefficients may differ from direct costs at Q0; those direct costs are not replaced
by the fitted prediction. Benchmark-only runs leave coefficient columns blank.
A flow_range approximation-only run has no direct reference calculation, so its
reference flow, direct annual cost and average cost fields are blank.

## Version 0.4.1: continue after a route calculation fails

The batch runner catches calculation errors for each route, records the message,
and continues with the next route. The existing engineering diameter-selection
procedure, cost equations, assumptions and input data are unchanged.

- The report's **summary** sheet includes every attempted route, with cost_status
  (ok or failed), cost_error, input_data_row, node identifiers, distance and input annual flow.
- The costed input copy records pipeline_cost_status and pipeline_cost_error.
- Failed routes have blank cost fields and no rows in coefficients, cost_components,
  design, fit_samples or fit_diagnostics. They are not zero-cost connections.
- Rows excluded by the input status/mode filters, or not selected by --row, retain
  blank pipeline_* result fields. The original input status column is preserved.
- The log and report settings record the number of successful and failed routes.
  No additional error file is created.

If all attempted routes fail, the runner still writes the summary, settings, units,
read_me and costed input copy (subject to the configured output switches).
Inactive cost/design sheets remain omitted. Existing pipeline_* cost fields are
cleared for failed and unprocessed rows, so old results cannot appear as current costs.

This behavior applies to approximation, benchmark and both modes. For flow_range,
a failed calculation at a required fitting/validation flow marks that route as failed;
no substitute flow or partial coefficients are exported. Direct Python cost calls
and run_pipeline_fixed_flow.py continue to raise/report calculation errors normally.

Configuration, input-reading/validation and output-writing errors still stop the run.
User interruption and memory exhaustion also stop it. There is no checkpoint/resume
facility; final outputs are written after all selected route calculations are attempted.
The batch CLI exits with code 0 when it writes its outputs, including runs containing
failed routes. Check the success/failure counts and the exported statuses before using costs.
Use only pipeline_cost_status=ok rows as calculated cost inputs to an optimiser.

Run the same full-batch command:

~~~bash
python -u run_pipeline_cost_model.py \
  --config config.parquet.example.yaml \
  --mode approximation \
  --coefficient-method fixed_design
~~~

Omit --row and set input_row: null in the YAML to process every eligible input row.
The short Uniper Maasvlakte -> Aramis case remains a recorded failure under the existing
engineering search; this update lets the other routes finish and be exported.

## One report workbook

| Sheet | Purpose |
|---|---|
| summary | One row per attempted connection, with status/error and direct totals where available |
| coefficients | Three additive contributions plus one clearly marked optimizer_total row per connection |
| cost_components | Additive component costs grouped by upfront CAPEX, annualised CAPEX, fixed OPEX and electricity OPEX |
| design | Selected diameter, thickness, steel grade, pressures, boosters, energy and reference assumptions |
| settings | Configuration and calculation conventions |
| units | Units of the exported fields |
| read_me | How to read and add the results |

The coefficients sheet contains annualized_capex, fixed_opex, electricity_opex and total.
Use **only component=total / row_role=optimizer_total** in the optimiser, or sum the three
contribution rows. Do not add both. The combined OPEX subtotal is not duplicated here.

The component sheet contains material, labour, ROW, miscellaneous, initial compression,
boosters, annualised capital, fixed maintenance and electricity. Rows are additive **within
their cost_group**. Annual cost is annualised CAPEX + fixed OPEX + electricity OPEX.
Never add upfront CAPEX to annualised CAPEX. Subtotals and totals are kept out of this
component table; summary owns the aggregate figures.

No direct-cost table is duplicated as a fixed_design_reference table.

| Run mode | Coefficients | Detailed cost components | Reference design / direct summary |
|---|---|---|---|
| approximation + fixed_design | Yes | No | Yes |
| approximation + flow_range | Yes | No | No; fitted designs are recorded in fit_samples |
| benchmark | No | Yes | Yes |
| both | Yes | Yes | Yes |

Only flow_range produces fit_samples and fit_diagnostics sheets. Inactive sheets/files
are omitted. The supplied mode=both provides both optimiser coefficients and the
SINTEF component breakdown.

The report is a static export of Python calculations. Rerun the model after changing
input assumptions. Per-route assumptions and operating limitations appear in design/settings.

## Fixed-design coefficients

At fixed distance L and reference annual transported mass Q0, the engineering model
selects one pipeline design. Annualise each asset using its own lifetime:

~~~text
CRF(r,n) = r * (1+r)^n / ((1+r)^n - 1)
annualised_CAPEX = CRF_pipe * CAPEX_pipe
                + CRF_compression * (CAPEX_initial + CAPEX_boosters)
F = annualised_CAPEX + annual_fixed_OPEX                         [EUR/year]
v = annual_electricity_OPEX(Q0) / Q0                             [EUR/tCO2]
annual_cost(Q) = F * built + v * Q                               [EUR/year]
average_total_cost(Q0) = F/Q0 + v                                [EUR/tCO2]
~~~

Capital and fixed maintenance remain included through F. Their fixed-design flow
coefficients are zero. Initial compression and booster electricity contribute to v.

No minimum/maximum fitting flow, fit method, fitting sample count or validation sample
count is used. The coefficients reconcile exactly at Q0. At other operating flows,
the approximation keeps the installed equipment and specific electricity consumption
constant; it does not re-size the pipeline or validate off-design hydraulics.
The prediction helper requires 0 <= Q <= Q0* built. It charges F for a built, idle asset
and zero for an unbuilt connection. Q0 is a conservative model limit, not a calculated
nameplate maximum. Recalculate to represent another investment capacity.

## Optional fit across pipeline capacities

Use coefficient_method: flow_range and explicit annual flow bounds to approximate the
cost of separately sized designs. config.flow_range.example.yaml shows this mode.
It supports endpoints or least_squares and independent interior validation samples.
Fit errors and negative coefficients are reported. Do not extrapolate beyond the bounds.

Flow bounds come from paired row-level annual_flow_min_t_per_year / annual_flow_max_t_per_year,
then the matching global YAML fields, then the legacy hourly capacity bounds converted
using operating_hours_per_year. The legacy default is a temporary 18–4050 t/h proxy;
supply defensible bounds for your connection.

For compatibility, an old YAML or direct PipelineCostConfig() call that omits
coefficient_method retains flow_range. All supplied normal-use configurations explicitly
choose fixed_design.

## Optional exports

CSV and JSON are off by default. Enable them only when required:

~~~yaml
write_csv: true
write_json: true
~~~

Equivalent CLI additions are --csv and --json. CSV writes one file for each active
report table. JSON contains the same tables once, with configuration and schema version;
it does not duplicate the reference cost ledger under separate approximation/benchmark objects.

write_xlsx controls the report workbook. write_costed_input independently controls the
copy of the source file. For just the costed input, set write_xlsx: false.
For just the report, set write_costed_input: false. At least one output must be enabled.

The output directory defaults to outputs/pipeline_costs, separate from older exports.
Only paths printed by the current run are its outputs. Existing unrelated files are not deleted.

## Direct calculation and Python API

~~~bash
python run_pipeline_fixed_flow.py --distance-km 200 --annual-flow-t-per-year 1000000 --output fixed_flow_result.json
~~~

This dedicated single-case CLI writes only the requested JSON, or prints JSON if --output
is omitted. It accepts operating-hour, electricity-price, discount-rate, pressure and terrain
options. --resistance is retained as optional routing metadata and has no financial effect.
It does not automatically load config.yaml. This legacy direct-result JSON and the Python
API retain EUR 2021 internal values; use the batch runner for the dual-year report and
costed-input outputs, or explicitly convert API monetary values with
pipeline_cost_model.currency.to_eur2024(value, config.conversion_factor).

~~~python
from pipeline_cost_model import calculate_pipeline_cost

result = calculate_pipeline_cost(distance_km=200, annual_flow_t_per_year=1_000_000)
pair = result.approximation.coefficients["baseline"]["total"]
F = pair.intercept_eur_per_year
v = pair.slope_eur_per_t
detailed_costs = result.benchmark.costs["baseline"]
~~~

Pass config=PipelineCostConfig(...) to change assumptions. This convenience function
always evaluates fixed_design and returns coefficients plus direct reference costs.
The Python result retains component ledgers; only baseline exists as a cost basis.

## Inputs and engineering assumptions

Supported formats: XLSX, CSV, Parquet and GeoParquet. Default input mappings are from_id,
to_id, distance_km and annual_flow_t_per_year. For XLSX the default sheet is
pipeline_route_metrics. annual_flow_t_per_year is required for fixed_design or benchmarks,
unless benchmark_annual_flow_t_per_year is supplied globally.

average_route_resistance is optional routing metadata. It is not a financial input.
distance_km must be the physical length of the routed connection, not an accumulated
resistance-weighted distance. Geometry is not used to re-route or infer Onshore/Offshore.
Specify terrain globally or with the per-row terrain field. Mixed onshore/offshore
segments are not automatically inferred or combined.

Supplied defaults: 8,000 h/year, 10% discount rate, 60 EUR 2021/MWh, 10 bar feed and 70 bar delivery.
Annual flow is converted to operating t/h by Q/H, then to kg/s by division by 3.6.
Pipeline/compression lifetimes are 50/25 years; annual fixed-OPEX fractions are 1.5%/4%.
Asset lifetimes and maintenance fractions come from OtherData.xlsx and are recorded in
the design output. Pressure support is 30 <= delivery < 90 bar and 0 < feed < delivery.
The adopted source/data and engineering corrections from version 0.3 are unchanged.

Capture, shipping, storage and sink-opening costs are outside this model.
The capital, maintenance and electricity assumptions should match the SINTEF case
before comparing the component breakdowns.

## Migration from version 0.3

- Spatial financial scaling and spatial cost results have been removed.
- Legacy spatial_fixed_opex_mode YAML settings are ignored with a notice.
- The old detailed_workbook_name YAML key is mapped to the single report workbook name.
- Optional CSV names and JSON schema now follow the report sheets described above.
- Costed inputs retain the nine original result fields and add four explicit EUR 2024 fields.
- Tests cover baseline accounting, resistance independence, both coefficient methods,
  row selection, component reconciliation in both price years, conversion provenance and
  overrides, failure handling, and preservation of costed input data/metadata.

Run the checks with python -m unittest discover -s tests -v.
