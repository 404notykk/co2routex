"""Direct one-scenario CLI using the same engine as the approximation runner."""
from __future__ import annotations
import argparse
import json
from dataclasses import asdict
from pathlib import Path
from pipeline_cost_model import PipelineCostConfig, calculate_pipeline_cost


def main():
    p = argparse.ArgumentParser(description="Annual fixed/per-tonne coefficients and detailed costs at one distance and annual flow")
    p.add_argument("--distance-km", type=float, required=True)
    p.add_argument("--annual-flow-t-per-year", type=float, required=True)
    p.add_argument("--resistance", type=float, default=1.0, help="Optional routing metadata; does not affect financial costs")
    p.add_argument("--operating-hours-per-year", type=float, default=8000.0)
    p.add_argument("--terrain", choices=["Onshore", "Offshore"], default="Onshore")
    p.add_argument("--timeframe", choices=["near-term", "mid-term", "long-term"], default="mid-term")
    p.add_argument("--electricity-price-eur-per-mwh", type=float, default=60.0)
    p.add_argument("--discount-rate", type=float, default=0.10)
    p.add_argument("--feed-pressure-bar", type=float, default=10.0)
    p.add_argument("--delivery-pressure-bar", type=float, default=70.0)
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    try:
        config = PipelineCostConfig(mode="benchmark", terrain=a.terrain, timeframe=a.timeframe,
            operating_hours_per_year=a.operating_hours_per_year,
            electricity_price_eur_per_mwh=a.electricity_price_eur_per_mwh,
            discount_rate=a.discount_rate, p_inlet_bar=a.feed_pressure_bar, p_outlet_bar=a.delivery_pressure_bar)
        result = calculate_pipeline_cost(a.distance_km, a.annual_flow_t_per_year,
                                         average_route_resistance=a.resistance, config=config)
        text = json.dumps(asdict(result), indent=2, allow_nan=False) + "\n"
    except (ValueError, RuntimeError) as exc:
        p.error(str(exc))
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(text, encoding="utf-8")
        print(f"Saved: {a.output}")
        for basis, components in result.approximation.coefficients.items():
            total = components["total"]
            print(f"{basis}: annual fixed cost = {total.intercept_eur_per_year:,.2f} EUR/year; "
                  f"flow coefficient = {total.slope_eur_per_t:.6f} EUR/tCO2")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
