"""Shared command-line behavior."""

from __future__ import annotations

import argparse
import logging
from dataclasses import replace
from pathlib import Path

from .config import TransportCostConfig
from .runner import run_with_outputs


def main(default_modes: tuple[str, ...] = ("truck", "railway")) -> None:
    parser = argparse.ArgumentParser(
        description="Calculate truck/railway EUR/t coefficients and annual costs from route tables or matrices"
    )
    parser.add_argument("--config", default="config.yaml", help="Path to YAML configuration")
    parser.add_argument("--input", type=Path, help="Override input path (relative to working directory)")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--row", type=int, help="1-based physical data row in a route table, before filtering")
    parser.add_argument("--transport-mode", choices=("truck", "road", "railway", "rail", "train", "both"))
    parser.add_argument("--annual-flow-t-per-year", type=float, help="Explicit fallback annual flow for rows without flow")
    parser.add_argument("--require-annual-flow", action="store_true", help="Fail routes without annual flow")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    config = TransportCostConfig.from_yaml(args.config, require_input=False)
    changes = {}
    if args.input is not None:
        changes.update(input_path=args.input.expanduser().resolve(), workbook_path=None)
    if args.output_dir is not None:
        changes["output_dir"] = args.output_dir.expanduser().resolve()
    if args.row is not None:
        changes["input_row"] = args.row
    if args.annual_flow_t_per_year is not None:
        changes["annual_flow_t_per_year"] = args.annual_flow_t_per_year
    if args.require_annual_flow:
        changes["require_annual_flow"] = True
    config = replace(config, **changes)
    modes = default_modes if args.transport_mode is None else (
        ("truck", "railway") if args.transport_mode == "both" else (args.transport_mode,)
    )
    for role, output in run_with_outputs(config, modes).items():
        print(f"{role}: {output}")
