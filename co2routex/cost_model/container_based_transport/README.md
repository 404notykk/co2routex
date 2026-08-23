# CO2RouteX container-based transport cost model

This folder generates simplified route-specific cost coefficients for
container-based CO2 transport by truck and railway. Routed distances are read
from the square `truck` and `railway` matrices in `node_metrics_routed.xlsx`.

## Cost equations

For truck transport:

```text
UC(d)     = 5.58 / d + 0.15                       EUR/(t*km)
gamma2(d) = UC(d) * d = 5.58 + 0.15 * d          EUR/t
```

For railway transport:

```text
UC(d)     = 28.9 / d + 0.07                       EUR/(t*km)
gamma2(d) = UC(d) * d = 28.9 + 0.07 * d          EUR/t
```

For both modes, `gamma1 = gamma3 = gamma4 = 0`. Distance is already embedded
in `gamma2` and must not be multiplied a second time. The cost for a directed
connection is:

```text
cost = gamma2 * transported_CO2
```

When transported CO2 is in t/year, the result is EUR/year. When it is an
hourly or representative-timestep flow, the downstream optimisation must apply
the corresponding timestep weights. Here `gamma2` is a transport tariff in
EUR/t, not an engineering CAPEX coefficient in EUR/(t/h).

## Input workbook

Each mode sheet must be a square directed matrix:

- row 1 contains destination node IDs;
- column A contains origin node IDs in the same order;
- a positive cell contains routed distance in km;
- `0` or a blank cell means the connection is unavailable.

The input workbook is never overwritten.

## Run

Copy the example configuration and set `workbook_path`:

```bash
cp config.example.yaml config.yaml
```

Generate both modes:

```bash
python run_transport_cost_model.py --config config.yaml
```

Or generate only one mode:

```bash
python run_truck_cost_model.py --config config.yaml
python run_railway_cost_model.py --config config.yaml
```

## Output workbook

The combined run creates `outputs/node_metrics_transport_costed.xlsx`. It
preserves the routed workbook and adds:

- `truck_gamma2`: directed EUR/t coefficient matrix;
- `railway_gamma2`: directed EUR/t coefficient matrix;
- `container_cost_coefficients`: auditable long-form route calculations;
- `container_cost_model_info`: equations, units and configuration notes.

Mode-specific runs use separate output names so that one does not overwrite the
other. Zero/unavailable connections remain zero in the coefficient matrices.

## Deliberate simplifications

This version does not add separate vehicle, wagon, loading, unloading,
access-leg, return-trip, compression, energy, fixed-OPEX or variable-OPEX costs.
The supplied unit-cost regressions represent the complete simplified transport
cost and are not combined with the old fixed ADOPT JSON coefficients.

## Test

```bash
pip install -e .[test]
python -m pytest
```
