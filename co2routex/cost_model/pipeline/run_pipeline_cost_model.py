"""CLI for direct benchmarks and annual transport-cost approximations."""
from __future__ import annotations
import argparse
import logging
from dataclasses import replace
from pipeline_cost_model import PipelineCostConfig
from pipeline_cost_model.runner import run

def main():
    parser = argparse.ArgumentParser(description="Pipeline annual costs: approximation, benchmark, or both")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--mode", choices=["approximation", "benchmark", "both"], help="Override the configured mode")
    parser.add_argument("--row", type=int, help="Run only this 1-based data-row position, before route filters; overrides input_row")
    parser.add_argument("--coefficient-method", choices=["fixed_design", "flow_range"], help="Override how the annual cost coefficients are obtained")
    parser.add_argument("--csv", action="store_true", help="Also export active report tables as CSV files")
    parser.add_argument("--json", action="store_true", help="Also export the report tables as JSON")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        config = PipelineCostConfig.from_yaml(args.config)
        if args.mode:
            config = replace(config, mode=args.mode)
        if args.row is not None:
            config = replace(config, input_row=args.row)
        if args.coefficient_method:
            config = replace(config, coefficient_method=args.coefficient_method)
        if args.csv:
            config = replace(config, write_csv=True)
        if args.json:
            config = replace(config, write_json=True)
        outputs = run(config)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(f"Results written for {config.mode} mode. Outputs: {config.output_dir}")
    for path in outputs.values():
        print(path)

if __name__ == "__main__":
    main()
