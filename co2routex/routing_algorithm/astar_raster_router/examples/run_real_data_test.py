from __future__ import annotations

import runpy
import sys
from pathlib import Path


# examples/run_real_data_test.py
EXAMPLES_DIR = Path(__file__).resolve().parent

# The astar_raster_router folder.
ROUTER_DIR = EXAMPLES_DIR.parent

# Repository root containing the outer co2routex package.
REPOSITORY_ROOT = ROUTER_DIR.parents[2]

CONFIG_PATH = ROUTER_DIR / "config.test.yaml"

MODULE_NAME = (
    "co2routex.routing_algorithm."
    "astar_raster_router.run_router"
)


def check_test_files() -> None:
    """Check that the required test configuration exists."""

    print(f"Router folder:    {ROUTER_DIR}")
    print(f"Repository root:  {REPOSITORY_ROOT}")
    print(f"Configuration:    {CONFIG_PATH}")

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            "The test configuration does not exist:\n"
            f"{CONFIG_PATH}\n\n"
            "Duplicate config.example.yaml and rename it "
            "config.test.yaml."
        )

    package_directory = REPOSITORY_ROOT / "co2routex"

    if not package_directory.exists():
        raise FileNotFoundError(
            "The co2routex package could not be found at:\n"
            f"{package_directory}\n\n"
            "Check the value of REPOSITORY_ROOT."
        )


def run_router() -> None:
    """Run the existing router entry point using config.test.yaml."""

    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))

    original_arguments = sys.argv.copy()

    sys.argv = [
        MODULE_NAME,
        "--config",
        str(CONFIG_PATH),
    ]

    try:
        runpy.run_module(
            MODULE_NAME,
            run_name="__main__",
        )
    except SystemExit as error:
        if error.code not in (None, 0):
            raise RuntimeError(
                f"The router failed with exit code {error.code}."
            ) from error
    finally:
        sys.argv = original_arguments


def main() -> None:
    print("=" * 60)
    print("A* RASTER ROUTER — REAL-DATA TEST")
    print("=" * 60)

    check_test_files()

    print("\nSetup check passed.")
    print("Starting routing calculation...\n")

    run_router()

    print("\n" + "=" * 60)
    print("ROUTER FINISHED SUCCESSFULLY")
    print("=" * 60)
    print(
        "Now inspect the output workbook and GIS route file "
        "in the output directory specified in config.test.yaml."
    )


if __name__ == "__main__":
    main()