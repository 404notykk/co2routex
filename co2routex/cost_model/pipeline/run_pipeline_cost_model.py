"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging

from pipeline_cost_model import PipelineCostConfig
from pipeline_cost_model.runner import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate route-specific pipeline CAPEX coefficients")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML configuration")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    config = PipelineCostConfig.from_yaml(args.config)
    main_output, details_output = run(config)
    print(f"Costed workbook: {main_output}")
    print(f"Detailed workbook: {details_output}")


if __name__ == "__main__":
    main()
