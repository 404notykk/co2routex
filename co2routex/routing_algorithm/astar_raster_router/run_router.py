"""Click Run in PyCharm; change CONFIG_PATH if required."""
from pathlib import Path
import logging
import time
from astar_router.cli import config_argument, load_settings
from astar_router.pipeline import run

CONFIG_PATH = Path(__file__).with_name("config.test.yaml")

def main():
    started = time.perf_counter()
    logging.basicConfig(level=logging.INFO,format="%(levelname)s: %(message)s")
    settings = load_settings(config_argument(CONFIG_PATH))
    records = run(settings)
    print(f"Elapsed after imports, including config and ALL output writes: {time.perf_counter()-started:.6f} s")
    print(f"Outputs: {settings.output_dir}")
    return 1 if any(r["status"] != "ok" for r in records) else 0

if __name__ == "__main__":
    raise SystemExit(main())
