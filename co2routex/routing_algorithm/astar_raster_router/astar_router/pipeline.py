"""Load once, validate once, route sequentially over the unrestricted grid."""
from collections import OrderedDict
from dataclasses import asdict
import logging
import platform
import socket
import time
import numpy as np
import rasterio
import pyproj
from .astar import NoPathError, search_prepared
from .inputs import load_network
from .models import SearchResult
from .raster import ResistanceRaster
from .monitoring import MemoryMonitor
from .outputs import write_routed_workbook, write_geopackage, write_benchmark

LOGGER = logging.getLogger(__name__)
GEOD = pyproj.Geod(ellps="WGS84")


def route_pair(connection, nodes, locations, raster, settings, cache, run_id, ordinal):
    started = time.perf_counter()
    origin, destination = nodes[connection.from_id], nodes[connection.to_id]
    a, b = locations[connection.from_id], locations[connection.to_id]
    start, goal = a["cell"], b["cell"]
    record = dict(run_id=run_id, pair_id=f"pair_{ordinal:05d}", mode=connection.mode,
                  from_id=connection.from_id,to_id=connection.to_id,
                  from_name=origin.node_name,to_name=destination.node_name,
                  straight_line_km=abs(GEOD.inv(origin.longitude,origin.latitude,
                                              destination.longitude,destination.latitude)[2])/1000,
                  from_cell_center_offset_m=a["snap_distance_m"],
                  to_cell_center_offset_m=b["snap_distance_m"],
                  status="ok",message="",result_source="computed")
    result = None
    key, reverse = (start, goal), (goal, start)
    if settings.cache_max_routes and key in cache:
        result = cache[key]; cache.move_to_end(key); record["result_source"] = "cache"
    elif settings.cache_max_routes and settings.cache_reverse_routes and reverse in cache:
        previous = cache[reverse]; cache.move_to_end(reverse)
        result = SearchResult(list(reversed(previous.path)), previous.accumulated_resistance,
                              previous.geometric_length_map_units, previous.explored_cells)
        record["result_source"] = "reverse_cache"
    record["pair_setup_wall_s"] = time.perf_counter()-started
    metrics = dict(search_setup_wall_s=0.0,search_wall_s=0.0,path_build_wall_s=0.0,
                   explored_cells=0,discovered_cells=0,queue_peak_entries=0)
    try:
        if result is None:
            result = search_prepared(raster.grid,start,goal,connectivity=settings.connectivity,
                                     prevent_corner_cutting=settings.prevent_corner_cutting,metrics=metrics)
        build_started = time.perf_counter()
        if settings.cache_max_routes and record["result_source"] == "computed":
            cache[key] = result
            while len(cache) > settings.cache_max_routes: cache.popitem(last=False)
        length = result.geometric_length_map_units
        # Vectorized affine conversion avoids one Rasterio call per path cell.
        cells = np.asarray(result.path, dtype=np.int64)
        rows, cols = cells[:,0]+0.5, cells[:,1]+0.5
        tr = raster.transform
        x = tr.a*cols + tr.b*rows + tr.c
        y = tr.d*cols + tr.e*rows + tr.f
        record.update(distance_km=length/1000,accumulated_resistance=result.accumulated_resistance,
                      average_route_resistance=result.accumulated_resistance/length if length else None,
                      path_cells=len(result.path),coordinates=list(zip(x.tolist(),y.tolist())))
        record["route_build_wall_s"] = time.perf_counter()-build_started
    except NoPathError as exc:
        record.update(status="no_path",message=str(exc),distance_km=None,
                      accumulated_resistance=None,average_route_resistance=None,path_cells=None,
                      coordinates=None,route_build_wall_s=0.0)
    record.update(metrics)
    # Include release of uncached path/search result in this pair's residual.
    del result
    record["pair_total_wall_s"] = time.perf_counter()-started
    components = ("pair_setup_wall_s","search_setup_wall_s","search_wall_s",
                  "path_build_wall_s","route_build_wall_s")
    record["pair_other_wall_s"] = record["pair_total_wall_s"] - sum(record[k] for k in components)
    return record


def run_with_timing(settings, *, run_label="run_01", write_timing_files=True):
    """Return (records, run-summary dict, settings/metadata dict).

    write_timing_files is retained as an API keyword; it now controls one XLSX,
    never CSV files. Imports/config parsing and report export are excluded from
    run_total_wall_s. The entry script prints command elapsed time after export.
    """
    settings.output_dir.mkdir(parents=True,exist_ok=True)
    started = time.perf_counter()
    summary = {"run_id":run_label}
    with MemoryMonitor(settings.rss_sample_interval_s) as monitor:
        tick = time.perf_counter(); nodes, connections = load_network(settings)
        summary["workbook_input_wall_s"] = time.perf_counter()-tick
        raster = ResistanceRaster(settings.raster_path,zero_is_barrier=settings.zero_is_barrier)
        summary.update(raster_read_wall_s=raster.read_wall_s,raster_prepare_wall_s=raster.prepare_wall_s)
        tick = time.perf_counter()
        locations = raster.node_cells(nodes,settings.nodes_crs,settings.snap_radius_cells)
        summary["node_prepare_wall_s"] = time.perf_counter()-tick
        summary["baseline_rss_mib"] = monitor.sample()
        cache = OrderedDict(); records = []
        tick = time.perf_counter()
        for number, connection in enumerate(connections,1):
            LOGGER.info("Routing %d/%d: %s -> %s",number,len(connections),connection.from_id,connection.to_id)
            baseline = monitor.begin_pair()
            try:
                record = route_pair(connection,nodes,locations,raster,settings,cache,run_label,number)
            finally:
                peak = monitor.end_pair()
            record.update(pair_start_rss_mib=baseline,pair_peak_rss_mib=peak)
            records.append(record)
        summary["all_pairs_wall_s"] = time.perf_counter()-tick
        cache.clear()
        tick = time.perf_counter(); write_routed_workbook(settings,records)
        summary["route_workbook_write_wall_s"] = time.perf_counter()-tick
        tick = time.perf_counter(); write_geopackage(settings,records,raster.crs)
        summary["gis_write_wall_s"] = time.perf_counter()-tick
        metadata = {**asdict(settings),**raster.metadata,
                    "hostname":socket.gethostname(),"python_version":platform.python_version(),
                    "rasterio_version":rasterio.__version__,"gdal_version":rasterio.__gdal_version__,
                    "pyproj_version":pyproj.__version__,"rss_sample_interval_s":settings.rss_sample_interval_s}
        for obsolete in ("route_timings_filename", "run_timings_filename"):
            metadata.pop(obsolete, None)
    summary["run_total_wall_s"] = time.perf_counter()-started
    keys = ("workbook_input_wall_s","raster_read_wall_s","raster_prepare_wall_s",
            "node_prepare_wall_s","all_pairs_wall_s","route_workbook_write_wall_s","gis_write_wall_s")
    summary["run_other_wall_s"] = summary["run_total_wall_s"] - sum(summary[k] for k in keys)
    summary.update(total_pairs=len(records),successful_pairs=sum(r["status"]=="ok" for r in records),
                   failed_pairs=sum(r["status"]!="ok" for r in records),
                   computed_pairs=sum(r["result_source"]=="computed" for r in records),
                   cached_pairs=sum(r["result_source"]!="computed" for r in records),
                   pairs_per_hour=3600*len(records)/summary["all_pairs_wall_s"] if summary["all_pairs_wall_s"] else 0,
                   total_search_wall_s=sum(r["search_wall_s"] for r in records),run_peak_rss_mib=monitor.peak)
    if write_timing_files:
        write_benchmark(settings.output_dir / "benchmark_results.xlsx",[summary],records,{run_label:metadata})
    return records, summary, metadata


def run(settings):
    return run_with_timing(settings)[0]
