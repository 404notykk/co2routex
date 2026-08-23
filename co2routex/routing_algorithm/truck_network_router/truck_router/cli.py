from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import load_settings
from .workflow import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Route eligible truck connections through country-specific OSM "
            "road networks and update the truck worksheet."
        )
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="config.yaml",
        help="YAML configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    settings = load_settings(Path(args.config))
    outputs = run(settings)
    logging.info("Updated workbook: %s", outputs["workbook_path"])
    logging.info("Route summary: %s", outputs["summary_path"])
    if outputs["routes_path"] is not None:
        logging.info("Route geometries: %s", outputs["routes_path"])
    else:
        logging.info("No successful route geometries were produced")
    return 0
