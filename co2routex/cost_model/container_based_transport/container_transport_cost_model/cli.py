"""Shared command-line behavior."""

from __future__ import annotations

import argparse
import logging

from .config import TransportCostConfig
from .runner import run


def main(default_modes: tuple[str, ...] = ("truck", "railway")) -> None:
    parser = argparse.ArgumentParser(
        description="Generate route-specific container transport gamma2 coefficients"
    )
    parser.add_argument("--config", default="config.yaml", help="Path to YAML configuration")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    config = TransportCostConfig.from_yaml(args.config)
    output = run(config, default_modes)
    print(f"Costed workbook: {output}")

