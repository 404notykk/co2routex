"""Run the connection algorithm against the configured real workbook."""

from __future__ import annotations

import sys
import time
from pathlib import Path


CONNECTION_ALGORITHM_DIR = Path(__file__).resolve().parent
ROUTING_ALGORITHM_DIR = CONNECTION_ALGORITHM_DIR.parent
CONFIG_PATH = CONNECTION_ALGORITHM_DIR / "config.test.yaml"


def prepare_imports() -> None:
    """Make the connection_algorithm package importable."""
    if str(ROUTING_ALGORITHM_DIR) not in sys.path:
        sys.path.insert(0, str(ROUTING_ALGORITHM_DIR))


def check_setup() -> None:
    """Check the configuration and required source files."""
    required_paths = [
        CONFIG_PATH,
        CONNECTION_ALGORITHM_DIR / "__init__.py",
        CONNECTION_ALGORITHM_DIR / "cli.py",
        CONNECTION_ALGORITHM_DIR / "generator.py",
        CONNECTION_ALGORITHM_DIR / "pipeline.py",
        CONNECTION_ALGORITHM_DIR / "rules.py",
    ]
    missing_paths = [path for path in required_paths if not path.exists()]

    if missing_paths:
        formatted = "\n".join(f"  - {path}" for path in missing_paths)
        raise FileNotFoundError(
            "The connection-algorithm setup is incomplete. "
            f"Missing paths:\n{formatted}"
        )


def run_test() -> None:
    """Load the configuration and update its workbook."""
    prepare_imports()

    from connection_algorithm.cli import load_settings
    from connection_algorithm.pipeline import run

    settings = load_settings(CONFIG_PATH)

    print(f"Workbook:        {settings.workbook_path}")
    print(f"Nodes sheet:     {settings.nodes_sheet}")
    print(f"Mode sheets:     {', '.join(settings.mode_sheets)}")
    print(f"Method:          {settings.method}")
    print(f"Emitter rule:    {settings.emitter_to_emitter_rule}")
    print()
    print(
        "WARNING: The configured mode sheets will be replaced in the "
        "workbook shown above."
    )
    print()

    started = time.perf_counter()
    result = run(settings)
    elapsed = time.perf_counter() - started

    print()
    print("=" * 60)
    print("CONNECTION ALGORITHM FINISHED SUCCESSFULLY")
    print("=" * 60)
    print(f"Nodes loaded:          {result.node_count}")
    print(f"Candidates generated:  {result.candidate_count}")
    print(f"Elapsed time:          {elapsed:.2f} seconds")
    print(f"Updated workbook:      {result.workbook_path}")

    if result.candidate_count == 0:
        print()
        print("WARNING: No candidate connections were generated.")
        print("Check node_type values and the configured direction rules.")


def main() -> None:
    """Check the setup and run the real-data test."""
    print("=" * 60)
    print("CONNECTION ALGORITHM — REAL-DATA TEST")
    print("=" * 60)
    print(f"Connection folder: {CONNECTION_ALGORITHM_DIR}")
    print(f"Routing folder:    {ROUTING_ALGORITHM_DIR}")
    print(f"Configuration:     {CONFIG_PATH}")
    print()

    check_setup()
    print("Setup check passed.")
    print("Starting connection generation...")
    print()
    run_test()


if __name__ == "__main__":
    main()
