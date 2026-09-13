"""One measured pass by default; all benchmark measurements in one XLSX."""
from pathlib import Path
from dataclasses import replace
import gc
import logging
import time
from astar_router.cli import config_argument, load_config
from astar_router.pipeline import run_with_timing
from astar_router.outputs import write_benchmark

CONFIG_PATH = Path(__file__).with_name("config.benchmark.yaml")

def main():
    started = time.perf_counter()
    logging.basicConfig(level=logging.INFO,format="%(levelname)s: %(message)s")
    settings, benchmark = load_config(config_argument(CONFIG_PATH))
    for i in range(benchmark["warmup_runs"]):
        run_with_timing(replace(settings,output_dir=settings.output_dir/f"warmup_{i+1:02d}"),
                        run_label=f"warmup_{i+1:02d}",write_timing_files=False)
        gc.collect()
    runs, records, metadata = [], [], {}
    for i in range(benchmark["repetitions"]):
        run_id = f"run_{i+1:02d}"
        output_dir = settings.output_dir if benchmark["repetitions"] == 1 else settings.output_dir/run_id
        pairs, summary, details = run_with_timing(replace(settings,output_dir=output_dir),
                                                 run_label=run_id,write_timing_files=False)
        # Do not retain coordinate arrays from completed runs for aggregation.
        records.extend({k:v for k,v in r.items() if k != "coordinates"} for r in pairs)
        runs.append(summary); metadata[run_id] = details
        del pairs
        gc.collect()
    report = settings.output_dir/"benchmark_results.xlsx"
    write_benchmark(report,runs,records,metadata)
    print(f"Benchmark workbook: {report}")
    print(f"Elapsed after imports, including warmups/config/aggregation/ALL outputs: {time.perf_counter()-started:.6f} s")
    print("Workbook run_total_wall_s excludes benchmark report export; see Field_guide.")
    return 1 if any(r["failed_pairs"] for r in runs) else 0

if __name__ == "__main__":
    raise SystemExit(main())
