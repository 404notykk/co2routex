# CO2RouteX pipeline cost model

This module converts routed pipeline metrics into route-specific linear CAPEX
coefficients that can later be passed to Adopt-Net0.

For a routed connection with installed capacity `Q` in tCO2/h:

```text
capex_baseline(Q) = baseline_slope * Q + baseline_intercept
capex_spatial(Q)  = average_route_resistance * capex_baseline(Q)
```

The spatial resistance multiplier is applied to the complete baseline CAPEX,
including the pipeline and compression components. No gamma terminology,
reference-resistance normalization, or resistance clipping is used inside this
module.

## Inputs

The input is the routed `node_metrics` workbook. Its
`pipeline_route_metrics` sheet must contain:

- `from_id`
- `to_id`
- `distance_km`
- `average_route_resistance`

If `status` and `mode` columns exist, only rows with `status = ok` and
`mode = pipeline` are evaluated.

The included `CO2IsothermalProperties.xlsx` and `OtherData.xlsx` files are the
engineering inputs required by the adopted Oeuvray calculation. The supplied
`CO2_Pipeline.json` is retained as an Adopt-Net0 interface reference; it is not
used to calculate the coefficients.

## Temporary capacity range

Version 0.1 defaults to 18--4050 tCO2/h. This is a configurable, temporary
DN-square-law proxy adopted before the full node inventory is available. The
Oeuvray calculation still chooses the engineering diameter internally for each
route and capacity.

Only the minimum and maximum capacity are evaluated. The straight line through
the two total-CAPEX endpoint values supplies the exported slope and intercept.
There is no diagnostic sampling in this version.

## Run

Create a working configuration:

```bash
cp config.example.yaml config.yaml
```

Edit `workbook_path`, then run from this folder:

```bash
python run_pipeline_cost_model.py --config config.yaml
```

## Outputs

`node_metrics_costed.xlsx` preserves the routed workbook and appends the
MILP-facing coefficients to `pipeline_route_metrics`.

`pipeline_cost_details.xlsx` is separate and contains:

- `route_coefficients`
- `endpoint_calculations`
- `configuration`

The output currency and cost basis are EUR 2021. The default terrain is
Onshore. The main coefficient names intentionally avoid Adopt-Net0's gamma
labels; an integration layer can map baseline or spatial slope/intercept to the
corresponding optimizer fields later.
