#!/usr/bin/env python3
"""Rasterise existing network GeoPackages within the optional water buffer.

Install beside rasterise_water_buffer_protected_area.py (shared domain/area
validation helpers). Python >=3.10; numpy, geopandas, pyogrio, shapely>=2.1,
pyproj, rasterio, affine and pandas. No download or bathymetry input required.
The original country sources and resistance rasters are never modified.

10 m GDAL all-touched line occupancy is weighted by exact water intersection
area, then aggregated to 100 m: 1 + (network multiplier - 1) * occupied fraction.
No physical line buffer is implied. Completed layer means multiply downstream.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import tempfile
import time

from affine import Affine
import numpy as np
import pyogrio
import rasterio
from rasterio.features import rasterize
from rasterio.windows import transform as window_transform
import shapely

import rasterise_water_buffer_protected_area as common

COUNTRIES = common.COUNTRIES
PIPELINE_DIR = common.PIPELINE_DIR
NODATA = common.NODATA
FEATURE_VALUES = {'motorways': 3.0, 'railways': 3.0, 'pipelines': 0.25}
METHOD = '10m_all_touched_exact_water_area_v1'
AREA_IMPLEMENTATION = 'halo_boundary_exact_areas_with_direct_recovery_v2'
AREA_ABSOLUTE_TOLERANCE = 1e-5  # m2; unchanged from the original conservation check.
AREA_RELATIVE_TOLERANCE = 1e-9


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', nargs='+', choices=COUNTRIES, default=list(COUNTRIES))
    parser.add_argument('--pipeline-dir', type=Path, default=PIPELINE_DIR)
    parser.add_argument('--buffer-root', type=Path)
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--buffer-km', type=common.positive_float, default=5.0)
    parser.add_argument('--block-size', type=common.block_size, default=128)
    parser.add_argument('--source-layer', default='lines')
    for name in FEATURE_VALUES:
        parser.add_argument(f'--{name}-dir', type=Path,
                            help=f'directory containing COUNTRY_{name}.gpkg')
    return parser


def resolve_paths(args):
    common.resolve_paths(args)
    for name in FEATURE_VALUES:
        directory = getattr(args, f'{name}_dir')
        setattr(args, f'{name}_dir', (directory or args.pipeline_dir/'intermediate'/f'existing_{name}').expanduser().resolve())
    return args


def inspect_sources(args, code):
    sources = {}
    for name in FEATURE_VALUES:
        path = getattr(args, f'{name}_dir')/f'{code}_{name}.gpkg'
        state = common.file_state(path)  # Missing files must never become neutral.
        metadata = common.file_metadata(path)
        info = pyogrio.read_info(path, layer=args.source_layer)
        if not info.get('crs'):
            raise ValueError(f'{path}: source CRS is missing')
        if 'LineString' not in str(info.get('geometry_type')):
            raise ValueError(f'{path}: expected a line layer, found {info.get("geometry_type")}')
        sources[name] = dict(path=path, state=state, metadata=metadata,
                             layer=args.source_layer, crs=str(info['crs']))
    assert_sources_unchanged(sources)
    return sources


def assert_sources_unchanged(sources):
    if any(common.file_state(s['path']) != s['state'] for s in sources.values()):
        raise RuntimeError('Network source changed during processing; previous outputs preserved')


def read_network(source):
    frame = pyogrio.read_dataframe(source['path'], layer=source['layer'], columns=[])
    for stage in ('source', 'projected'):
        if stage == 'projected':
            frame = frame.to_crs(common.TARGET_CRS)
        if (frame.crs is None or frame.geometry.isna().any() or frame.geometry.is_empty.any()
                or not frame.geometry.is_valid.all()
                or not frame.geom_type.isin(['LineString', 'MultiLineString']).all()
                or not np.isfinite(shapely.get_coordinates(frame.geometry.to_numpy())).all()):
            raise ValueError(f'{source["path"]}: invalid {stage} line geometry')
    geometries = frame.geometry.to_numpy()
    return geometries, shapely.STRtree(geometries)


def line_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'LineString':
        return [geometry] if geometry.length > 0 else []
    if geometry.geom_type in ('MultiLineString', 'GeometryCollection'):
        return [part for child in geometry.geoms for part in line_parts(child)]
    return []  # Isolated point contact has no line segment to rasterise.


def _water_coverage_area(geometry, shape, transform, included):
    """Measure water area; rasterisation only accelerates interior classification.

Polygon and boundary-line rasterisation can disagree for nearly grid-aligned
edges. Use a one-pixel halo and neighbouring boundary pixels as conservative
candidates for exact intersection. Only the candidate set expands: the water
geometry, cell sizes and intersection areas are never buffered or altered.
"""
    result = np.zeros(shape, dtype='float64')
    if geometry.is_empty or not included.any():
        return result
    height, width = shape
    halo_transform = transform * Affine.translation(-1, -1)
    halo_shape = (height+2, width+2)
    covered = rasterize([(geometry, 1)], out_shape=halo_shape, transform=halo_transform,
                        fill=0, dtype='uint8', all_touched=True)[1:-1, 1:-1].astype(bool)
    cell_area = abs(transform.a*transform.e)
    result[covered & included] = cell_area
    edges = rasterize([(geometry.boundary, 1)], out_shape=halo_shape, transform=halo_transform,
                      fill=0, dtype='uint8', all_touched=True).astype(bool)
    boundary_candidates = np.zeros(shape, dtype=bool)
    for row_shift in range(3):
        for col_shift in range(3):
            boundary_candidates |= edges[row_shift:row_shift+height, col_shift:col_shift+width]
    rows, cols = np.nonzero(boundary_candidates & included)
    for start in range(0, len(rows), common.QUERY_CELLS):
        rr, cc = rows[start:start+common.QUERY_CELLS], cols[start:start+common.QUERY_CELLS]
        x, y = transform.c+cc*transform.a, transform.f+rr*transform.e
        cells = shapely.box(x, y+transform.e, x+transform.a, y)
        result[rr, cc] = shapely.area(shapely.intersection(cells, geometry))
    tolerance = max(common.AREA_TOLERANCE_M2, cell_area*1e-9)
    if (not np.isfinite(result).all() or np.any(result < -tolerance)
            or np.any(result > cell_area+tolerance)):
        raise ValueError('Invalid exact water-cell intersection area')
    return np.clip(result, 0, cell_area)


def _direct_water_partitions(domain, transform, area, weights, suspect, diagnostics=None):
    """Recompute each suspect cell and its 100 subcells from one local polygon.

Do not renormalise weights or relax tolerance to hide an area discrepancy.
    The raster-assisted routine is bypassed for all recalculated areas.
With weights=None, only the coarse area is requested.
"""
    rows, cols = np.nonzero(suspect)
    if diagnostics is not None:
        key = 'direct_partition_recomputed_cells' if weights is not None else 'direct_coarse_recomputed_cells'
        diagnostics[key] = diagnostics.get(key, 0) + len(rows)
    subrows, subcols = np.indices((10, 10))
    dx, dy = transform.a, transform.e
    for start in range(0, len(rows), 64):
        rr, cc = rows[start:start+64], cols[start:start+64]
        x, y = transform.c + cc*dx, transform.f + rr*dy
        cells = shapely.box(x, y+dy, x+dx, y)
        cell_water = shapely.intersection(cells, domain)
        direct_area = shapely.area(cell_water)
        invalid = (~np.isfinite(direct_area) | (direct_area <= 0)
                   | (direct_area > abs(dx*dy) + AREA_ABSOLUTE_TOLERANCE))
        direct_fine = None
        direct_sum = None
        if weights is not None:
            xx = x[:, None, None] + subcols[None, :, :]*(dx/10)
            yy = y[:, None, None] + subrows[None, :, :]*(dy/10)
            fine_cells = shapely.box(xx, yy+dy/10, xx+dx/10, yy)
            direct_fine = shapely.area(shapely.intersection(fine_cells, cell_water[:, None, None]))
            direct_sum = direct_fine.sum(axis=(1, 2), dtype=np.float64)
            invalid |= (~np.isfinite(direct_fine).all(axis=(1, 2))
                        | np.any(direct_fine < 0, axis=(1, 2))
                        | np.any(direct_fine > abs(dx*dy)/100 + AREA_ABSOLUTE_TOLERANCE, axis=(1, 2))
                        | ~np.isclose(direct_sum, direct_area,
                                      atol=AREA_ABSOLUTE_TOLERANCE, rtol=AREA_RELATIVE_TOLERANCE))
        if np.any(invalid):
            j = int(np.flatnonzero(invalid)[0])
            fine_message = f', direct_fine_sum_m2={direct_sum[j]:.12g}' if direct_sum is not None else ''
            raise ValueError('Water area remains inconsistent after direct vector intersections: '
                             f'local row={int(rr[j])}, col={int(cc[j])}, '
                             f'cell_bounds={list(shapely.bounds(cells[j]))}, '
                             f'direct_coarse_m2={direct_area[j]:.12g}' + fine_message)
        area[rr, cc] = direct_area
        if weights is not None:
            weights[rr[:, None, None]*10 + subrows, cc[:, None, None]*10 + subcols] = direct_fine


def network_block(networks, water, included, transform, diagnostics=None):
    """Return three factor arrays, exact eligible-water area, occupied areas.

Networks maps each feature to (geometry array, STRtree). A shared fine-water
area array is reused across features. A 10 m halo avoids block-edge clipping.
"""
    shape = included.shape
    fine_transform = transform * Affine.scale(0.1)
    fine_shape = (shape[0]*10, shape[1]*10)
    halo_transform = fine_transform * Affine.translation(-1, -1)
    halo_shape = (fine_shape[0]+2, fine_shape[1]+2)
    footprint = shapely.box(halo_transform.c, halo_transform.f-halo_shape[0]*10,
                            halo_transform.c+halo_shape[1]*10, halo_transform.f)
    domain = common._polygon_union([shapely.intersection(water, footprint)])
    area = _water_coverage_area(domain, shape, transform, included)
    zero_area = included & (area <= 0)
    if zero_area.any():
        _direct_water_partitions(domain, transform, area, None, zero_area, diagnostics)
    clipped_by_feature = {}
    for name, (geometries, tree) in networks.items():
        ids = tree.query(footprint, predicate='intersects')
        # Clip before allocating fine arrays: land-only candidates do not need
        # detailed water-area calculations or contribute to occupancy.
        clipped_by_feature[name] = [part for g in shapely.intersection(geometries[ids], domain)
                                    for part in line_parts(g)]
    weights = None
    if any(clipped_by_feature.values()):
        fine_included = np.repeat(np.repeat(included, 10, axis=0), 10, axis=1)
        weights = _water_coverage_area(domain, fine_shape, fine_transform, fine_included)
        fine_total = weights.reshape(shape[0], 10, shape[1], 10).sum(axis=(1, 3))
        suspect = included & ~np.isclose(fine_total, area,
                                         atol=AREA_ABSOLUTE_TOLERANCE, rtol=AREA_RELATIVE_TOLERANCE)
        if suspect.any():
            if diagnostics is not None:
                diagnostics['maximum_initial_partition_discrepancy_m2'] = max(
                    diagnostics.get('maximum_initial_partition_discrepancy_m2', 0.0),
                    float(np.max(np.abs(fine_total[suspect]-area[suspect]))))
            _direct_water_partitions(domain, transform, area, weights, suspect, diagnostics)
            fine_total = weights.reshape(shape[0], 10, shape[1], 10).sum(axis=(1, 3))
        if not np.allclose(fine_total, area, atol=AREA_ABSOLUTE_TOLERANCE, rtol=AREA_RELATIVE_TOLERANCE):
            raise ValueError('Fine water areas do not sum to coarse water area after direct recovery')
    outputs, occupied = {}, {}
    for name, value in FEATURE_VALUES.items():
        affected = np.zeros(shape, dtype='float64')
        clipped = clipped_by_feature[name]
        if clipped:
            # Duplicate features burn the same binary value.
            present = rasterize(((g, 1) for g in clipped), out_shape=halo_shape,
                                transform=halo_transform, fill=0, dtype='uint8',
                                all_touched=True)[1:-1, 1:-1]
            affected = (present*weights).reshape(shape[0], 10, shape[1], 10).sum(axis=(1, 3))
        if np.any(affected > area + 1e-5):
            raise ValueError('Occupied water area exceeds the water domain')
        affected = np.minimum(affected, area)
        result = np.full(shape, NODATA, dtype='float32')
        fraction = np.clip(affected[included]/area[included], 0, 1)
        result[included] = np.clip(1+(value-1)*fraction, min(1, value), max(1, value))
        outputs[name], occupied[name] = result, affected
    return outputs, area, occupied


def rasterise_country(args, code, prepared=None, sources=None):
    started = time.perf_counter()
    prepared = prepared or common.load_country_inputs(args, code)
    sources = sources or inspect_sources(args, code)
    common._assert_country_unchanged(prepared, code)
    assert_sources_unchanged(sources)
    print(f'{code}: loading existing network GeoPackages', flush=True)
    networks = {name: read_network(source) for name, source in sources.items()}
    grid, transform, water = prepared['grid'], prepared['transform'], prepared['water']
    outdir = args.output_root/code/'raster'
    outdir.mkdir(parents=True, exist_ok=True)
    names = {name: f'{code}_water_buffer_{name}_resistance_100m.tif' for name in FEATURE_VALUES}
    report_name = f'{code}_water_buffer_network_resistance.json'
    counts = dict(eligible_cells=0, processed_blocks=0, skipped_blocks=0, partial_water_cells=0,
                  direct_partition_recomputed_cells=0, direct_coarse_recomputed_cells=0,
                  maximum_initial_partition_discrepancy_m2=0.0)
    stats = {name: dict(count=0, minimum=math.inf, maximum=-math.inf, total=0.0,
                       occupied_water_m2=0.0, influenced_cells=0) for name in FEATURE_VALUES}
    area_total = 0.0
    last_progress = time.perf_counter()
    total_blocks = math.ceil(grid['width']/args.block_size)*math.ceil(grid['height']/args.block_size)
    print(f'{code}: three network factors; {prepared["expected_eligible_cells"]:,} eligible cells', flush=True)
    with tempfile.TemporaryDirectory(prefix=f'{code}_networks_', dir=outdir) as directory:
        stage = Path(directory)
        with ExitStack() as stack:
            mask = stack.enter_context(rasterio.open(prepared['paths']['mask']))
            writers = {name: stack.enter_context(rasterio.open(stage/filename, 'w', **common.raster_profile(grid)))
                       for name, filename in names.items()}
            for window in common.windows(grid['width'], grid['height'], args.block_size):
                included = common._checked_mask(mask.read(1, window=window), water, transform, window, code)
                count = int(included.sum())
                counts['eligible_cells'] += count
                counts['processed_blocks'] += 1
                if count:
                    previous_recomputed = counts['direct_partition_recomputed_cells'] + counts['direct_coarse_recomputed_cells']
                    try:
                        outputs, area, occupied = network_block(networks, water, included,
                            window_transform(window, transform), diagnostics=counts)
                    except ValueError as exc:
                        raise ValueError(f'{code}: block row={int(window.row_off)}, '
                                         f'col={int(window.col_off)}: {exc}') from exc
                    recomputed = counts['direct_partition_recomputed_cells'] + counts['direct_coarse_recomputed_cells'] - previous_recomputed
                    if recomputed:
                        print(f'{code}: directly recalculated water areas for {recomputed} cell checks '
                              f'in block row={int(window.row_off)}, col={int(window.col_off)}', flush=True)
                    area_total += float(area.sum())
                    counts['partial_water_cells'] += int(np.count_nonzero(included & (area < 10000-1e-6)))
                    for name, output in outputs.items():
                        values = output[included]
                        low, high = sorted((1, FEATURE_VALUES[name]))
                        if not np.isfinite(values).all() or np.any((values < low) | (values > high)):
                            raise ValueError(f'{code}/{name}: invalid factors')
                        s = stats[name]
                        s['count'] += count
                        s['minimum'] = min(s['minimum'], float(values.min()))
                        s['maximum'] = max(s['maximum'], float(values.max()))
                        s['total'] += float(values.sum(dtype=np.float64))
                        s['occupied_water_m2'] += float(occupied[name].sum())
                        s['influenced_cells'] += int(np.count_nonzero(included & (occupied[name] > 0)))
                else:
                    counts['skipped_blocks'] += 1
                    outputs = {name: np.full(included.shape, NODATA, dtype='float32') for name in names}
                for name, output in outputs.items():
                    writers[name].write(output, 1, window=window)
                if time.perf_counter()-last_progress >= 25:
                    print(f'{code}: {counts["processed_blocks"]:,}/{total_blocks:,} blocks; '
                          f'{counts["eligible_cells"]:,} eligible cells written', flush=True)
                    last_progress = time.perf_counter()
            for name, writer in writers.items():
                writer.update_tags(country=code, feature=name, method=METHOD, units='dimensionless',
                                   area_implementation=AREA_IMPLEMENTATION,
                                   formula='1 + (feature_multiplier - 1) * occupied_water_fraction',
                                   feature_multiplier=str(FEATURE_VALUES[name]), neutral='1',
                                   occupancy='10 m GDAL all_touched lines, no physical buffer',
                                   denominator='Exact water area within eligible 100 m cell',
                                   source_completeness='Not independently verified')
        if counts['eligible_cells'] != prepared['expected_eligible_cells']:
            raise ValueError(f'{code}: eligible count differs from domain report')
        common._assert_country_unchanged(prepared, code)
        assert_sources_unchanged(sources)
        layers = {}
        for name, s in stats.items():
            number = s['count']
            signature = common.file_metadata(stage/names[name])
            signature['path'] = str(outdir/names[name])
            layers[name] = dict(feature_multiplier=FEATURE_VALUES[name],
                               source={**sources[name]['metadata'], 'layer': sources[name]['layer'],
                                       'crs': sources[name]['crs'], 'features': len(networks[name][0])},
                               raster_signature=signature,
                               statistics=dict(count=number, minimum=s['minimum'] if number else None,
                                               maximum=s['maximum'] if number else None,
                                               mean=s['total']/number if number else None),
                               class_cells=dict(no_mapped_occupancy=number-s['influenced_cells'],
                                                mapped_occupancy=s['influenced_cells']),
                               class_percentages=dict(no_mapped_occupancy=100*(number-s['influenced_cells'])/number if number else None,
                                                      mapped_occupancy=100*s['influenced_cells']/number if number else None),
                               occupied_water_km2=s['occupied_water_m2']/1e6,
                               occupied_water_percent=100*s['occupied_water_m2']/area_total if area_total else None)
        report = dict(schema_version=1, report_type='water_buffer_networks', method=METHOD,
                      area_implementation=AREA_IMPLEMENTATION,
                      country=code, buffer_km=args.buffer_km, created_utc=datetime.now(timezone.utc).isoformat(),
                      status='water_buffer_networks_prepared' if counts['eligible_cells'] else 'no_eligible_cell_centres',
                      grid=grid, inputs=prepared['inputs'], layers=layers, diagnostics=counts,
                      eligible_water_km2=area_total/1e6,
                      rules=dict(eligibility=common.ELIGIBILITY, neutral=1, nodata=NODATA,
                                 occupancy='Binary 10 m all-touched line occupancy; duplicates counted once',
                                 physical_buffer_m=0, halo_fine_pixels=1,
                                 line_clip='Unified mapped-water geometry before rasterisation',
                                 denominator='Exact water intersection area; not the diagnostic fraction raster',
                                 aggregation='1 + (feature_multiplier - 1) * occupied_water_area / water_area',
                                 missing_source='Error; never converted to neutral',
                                 empty_intersection='Neutral 1; no claim of complete mapping'),
                      coverage=dict(independent_network_completeness_verified=False,
                                    source_selection='Existing per-country GeoPackages; no redownload or new tag filtering',
                                    meaning='Infrastructure mapped in available Geofabrik-derived extracts'),
                      processing=dict(block_size_100m=args.block_size, processing_seconds=time.perf_counter()-started,
                                      dependencies=common.dependency_versions(),
                                      memory='Projected country network lines and spatial indexes in memory; fine raster arrays blockwise'),
                      validation=dict(mask_equals_strict_vector_centres=True, sha256_content_provenance=True,
                                      water_boundary_candidates='One-pixel halo and neighbouring boundary pixels; exact vector areas for candidates; geometry not buffered',
                                      water_area_conservation='Disagreeing coarse/fine areas recalculated directly from each cell-water polygon; original tolerances retained; weights not renormalised',
                                      publication='All outputs staged; per-file atomic replacement; report last; one writer per country'),
                      outputs={**{name: str(outdir/filename) for name, filename in names.items()},
                               'report': str(outdir/report_name)})
        (stage/report_name).write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        for filename in list(names.values())+[report_name]:
            (stage/filename).replace(outdir/filename)
    print(f'{code}: three network rasters saved; {outdir/report_name}', flush=True)
    return report


def main(argv=None):
    args = resolve_paths(build_parser().parse_args(argv))
    if tuple(int(v) for v in shapely.__version__.split('.')[:2]) < (2, 1):
        raise RuntimeError('This script requires shapely>=2.1')
    # Cheap source metadata/hash preflight for all requested countries before writing.
    sources = {code: inspect_sources(args, code) for code in args.countries}
    for code in args.countries:
        rasterise_country(args, code, sources=sources[code])


if __name__ == '__main__':
    main()
