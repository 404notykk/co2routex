#!/usr/bin/env python3
"""Rasterise an existing vector water buffer on the nested 100 m / 10 m grid.

Install in data_processing/spatial_resistance/water_buffer/. Python >=3.10.
Dependencies: numpy, rasterio, affine, geopandas, pyogrio, pyproj, shapely>=2.1.
No downloads, LCM reads, population raster reads or original-domain edits.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import tempfile
import time

from affine import Affine
import geopandas as gpd
import numpy as np
from pyproj import CRS
import rasterio
from rasterio.windows import Window
from shapely import contains_xy, get_coordinates, prepare, union_all
from shapely.geometry import GeometryCollection, box

SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SCRIPT_DIR.parents[2] / 'database' / 'pipeline'
COUNTRIES = ('NL', 'DE', 'NO')
TARGET_CRS = CRS.from_epsg(3035)
FACTOR = 10
NODATA = -9999.0
WATER_MULTIPLIER = 10.0
POPULATION_MULTIPLIER = 1.0
WATER_CATEGORY = 'One combined water buffer; all selected water receives the same treatment'


def positive_float(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError('must be finite and greater than zero')
    return value


def block_size(value):
    try:
        value = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('must be an integer from 1 to 512') from exc
    if not 1 <= value <= 512:
        raise argparse.ArgumentTypeError('must be an integer from 1 to 512')
    return value


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', nargs='+', choices=COUNTRIES, default=list(COUNTRIES))
    parser.add_argument('--pipeline-dir', type=Path, default=PIPELINE_DIR)
    parser.add_argument('--domain-root', type=Path, help='scenario root containing COUNTRY/domain/')
    parser.add_argument('--reference-dir', type=Path)
    parser.add_argument('--output-root', type=Path, help='scenario root for COUNTRY/raster/')
    parser.add_argument('--buffer-km', type=positive_float, default=5.0)
    parser.add_argument('--block-size', type=block_size, default=128,
                        help='100 m cells along each block edge (1..512; default: 128)')
    return parser


def resolve_paths(args):
    args.countries = list(dict.fromkeys(args.countries))
    args.pipeline_dir = args.pipeline_dir.expanduser().resolve()
    scenario = args.pipeline_dir / 'intermediate' / 'water_buffer' / f'{args.buffer_km:g}km'
    args.domain_root = (args.domain_root or scenario).expanduser().resolve()
    args.output_root = (args.output_root or args.domain_root).expanduser().resolve()
    args.reference_dir = (args.reference_dir or args.pipeline_dir / 'intermediate' / 'reference_grid').expanduser().resolve()
    return args


def input_paths(args, code):
    directory = args.domain_root / code / 'domain'
    return dict(gpkg=directory / f'{code}_water_buffer.gpkg',
                domain_report=directory / f'{code}_water_buffer.json',
                reference=args.reference_dir / f'{code}_reference_grid.json')


def file_metadata(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(data)
    stat = path.stat()
    return dict(path=str(path.resolve()), size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns, sha256=digest.hexdigest())


def positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{label} must be a positive integer')
    return value


def validate_grid(grid, label):
    if CRS.from_user_input(grid.get('crs')) != TARGET_CRS:
        raise ValueError(f'{label}: expected EPSG:3035')
    for name in ('width', 'height', 'fine_width', 'fine_height'):
        positive_integer(grid.get(name), f'{label}/{name}')
    transform = np.asarray(grid.get('transform'), dtype=float)
    fine = np.asarray(grid.get('fine_transform'), dtype=float)
    if (transform.shape != (6,) or not np.isfinite(transform).all()
            or not np.allclose(transform[[0, 1, 3, 4]], [100, 0, 0, -100], rtol=0, atol=1e-8)):
        raise ValueError(f'{label}: expected finite north-up 100 m transform')
    expected = transform.copy()
    expected[[0, 1, 3, 4]] /= FACTOR
    if (grid.get('factor') != FACTOR or fine.shape != (6,)
            or not np.isfinite(fine).all()
            or not np.allclose(fine, expected, rtol=0, atol=1e-8)
            or grid['fine_width'] != FACTOR * grid['width']
            or grid['fine_height'] != FACTOR * grid['height']):
        raise ValueError(f'{label}: 10 m grid must nest exactly in 100 m cells')
    return Affine(*transform)


def read_polygon_layer(path, layer, allow_empty=False):
    frame = gpd.read_file(path, layer=layer)
    if frame.crs is None or CRS.from_user_input(frame.crs) != TARGET_CRS:
        raise ValueError(f'{path}/{layer}: expected EPSG:3035')
    geometries = []
    for index, geometry in enumerate(frame.geometry):
        if geometry is None:
            raise ValueError(f'{path}/{layer}, row {index}: missing geometry')
        if geometry.is_empty:
            if allow_empty:
                continue
            raise ValueError(f'{path}/{layer}, row {index}: empty geometry')
        if (geometry.geom_type not in ('Polygon', 'MultiPolygon')
                or not geometry.is_valid or not np.isfinite(get_coordinates(geometry)).all()):
            raise ValueError(f'{path}/{layer}, row {index}: invalid or non-finite polygon geometry')
        geometries.append(geometry)
    if not geometries:
        if allow_empty:
            return GeometryCollection()
        raise ValueError(f'{path}/{layer}: no polygon area')
    # Preparation already writes one dissolved multipart geometry per layer.
    geometry = geometries[0] if len(geometries) == 1 else union_all(geometries)
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError(f'{path}/{layer}: invalid polygon union')
    return geometry


def area_tolerance(area):
    return max(0.1, float(area) * 1e-10)  # m²; accommodate only numerical roundoff.


def load_country_inputs(args, code):
    paths = input_paths(args, code)
    report = json.loads(paths['domain_report'].read_text())
    reference = json.loads(paths['reference'].read_text())
    if report.get('schema_version') != 2 or report.get('country') != code:
        raise ValueError(f'{code}: expected schema 2 water-buffer report for this country')
    if reference.get('schema_version') != 1 or reference.get('country') != code:
        raise ValueError(f'{code}: expected schema 1 reference grid for this country')
    if (not isinstance(report.get('buffer_km'), (int, float))
            or not math.isclose(report['buffer_km'], args.buffer_km, rel_tol=0, abs_tol=1e-10)):
        raise ValueError(f'{code}: requested buffer distance differs from vector report')
    if CRS.from_user_input(report.get('crs')) != TARGET_CRS:
        raise ValueError(f'{code}: vector report must specify EPSG:3035')
    method = report.get('method', {})
    if method.get('water_category') != WATER_CATEGORY:
        raise ValueError(f'{code}: vector report must use the combined-water domain policy')
    excluded = set(method.get('excluded_facility_codes', []))
    if not {'swimming_pool', 'wastewater', 'sewage'}.issubset(excluded):
        raise ValueError(f'{code}: vector report does not declare required facility exclusions')
    grid = report['grid']
    transform = validate_grid(grid, f'{code}/expanded grid')
    ref_transform = validate_grid(reference, f'{code}/reference grid')
    boundary = read_polygon_layer(paths['gpkg'], 'country_boundary')
    strip = read_polygon_layer(paths['gpkg'], 'outer_strip')
    water = read_polygon_layer(paths['gpkg'], 'water_buffer', allow_empty=True)
    expected_status = 'no_mapped_water_selected' if water.is_empty else 'mapped_water_domain_prepared'
    if report.get('status') != expected_status:
        raise ValueError(f'{code}: vector report status does not match the saved water geometry')
    tolerance = area_tolerance(strip.area)
    if strip.intersection(boundary).area > tolerance:
        raise ValueError(f'{code}: saved outer strip overlaps the original country')
    if water.difference(strip).area > tolerance:
        raise ValueError(f'{code}: saved water extends beyond the outer strip')
    if water.intersection(boundary).area > tolerance:
        raise ValueError(f'{code}: saved water overlaps the original country')
    # Confirm this is actually the requested outer strip, rather than accepting
    # arbitrary geometry merely because its bounding rectangle happens to match.
    quad_segs = positive_integer(report.get('buffer_quad_segs'), f'{code}/buffer_quad_segs')
    expected_strip = boundary.buffer(args.buffer_km * 1000, quad_segs=quad_segs).difference(boundary)
    if expected_strip.symmetric_difference(strip).area > tolerance:
        raise ValueError(f'{code}: saved outer strip differs from the country buffer rule')
    measured = dict(country=boundary.area, outer_strip=strip.area, water_buffer=water.area,
                    not_selected_as_water=strip.area-water.area)
    for name, area in measured.items():
        recorded = report.get('area_km2', {}).get(name)
        if (isinstance(recorded, bool) or not isinstance(recorded, (int, float))
                or not math.isfinite(recorded) or recorded < 0
                or abs(recorded * 1e6-area) > area_tolerance(area)):
            raise ValueError(f'{code}: {name} vector area differs from the report')
    xmin, ymin, xmax, ymax = strip.bounds
    c0 = math.floor((xmin-ref_transform.c)/100)
    c1 = math.ceil((xmax-ref_transform.c)/100)
    r0 = math.floor((ref_transform.f-ymax)/100)
    r1 = math.ceil((ref_transform.f-ymin)/100)
    expected_transform = ref_transform * Affine.translation(c0, r0)
    for key, expected in (('reference_column_offset', c0), ('reference_row_offset', r0)):
        value = grid.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value != expected:
            raise ValueError(f'{code}: expanded-grid {key} is inconsistent')
    if (grid['width'] != c1-c0 or grid['height'] != r1-r0
            or not np.allclose(list(transform)[:6], list(expected_transform)[:6], rtol=0, atol=1e-7)):
        raise ValueError(f'{code}: expanded grid does not match full strip bounds on current reference lattice')
    if c0 > 0 or r0 > 0 or c1 < reference['width'] or r1 < reference['height']:
        raise ValueError(f'{code}: expanded grid does not contain the current reference rectangle')
    metadata = {name: file_metadata(path) for name, path in paths.items()}
    return dict(report=report, reference=reference, grid=grid, water=water,
                boundary=boundary, strip=strip, inputs=metadata)


def windows(width, height, size):
    for row in range(0, height, size):
        for column in range(0, width, size):
            yield Window(column, row, min(size, width-column), min(size, height-row))


def sample_block(water, transform, window):
    """Strict vector-centre tests; coarse centres are NOT fine-grid samples."""
    width, height = int(window.width), int(window.height)
    origin_x, origin_y = transform * (window.col_off, window.row_off)
    x = origin_x + (np.arange(width) + 0.5) * 100
    y = origin_y - (np.arange(height) + 0.5) * 100
    eligible = contains_xy(water, x[None, :], y[:, None])
    fine_x = origin_x + (np.arange(width * FACTOR) + 0.5) * 10
    fine_y = origin_y - (np.arange(height * FACTOR) + 0.5) * 10
    fine = contains_xy(water, fine_x[None, :], fine_y[:, None])
    counts = fine.reshape(height, FACTOR, width, FACTOR).sum(axis=(1, 3), dtype=np.uint16)
    fraction = counts.astype('float32') / (FACTOR * FACTOR)
    return eligible, fraction


def raster_profile(grid, dtype, nodata):
    return dict(driver='GTiff', width=grid['width'], height=grid['height'], count=1,
                crs='EPSG:3035', transform=Affine(*grid['transform']), dtype=dtype,
                nodata=nodata, tiled=True, blockxsize=256, blockysize=256,
                compress='deflate', predictor=1 if dtype == 'uint8' else 3,
                BIGTIFF='IF_SAFER')


def dependency_versions():
    result = {}
    for package in ('numpy', 'rasterio', 'affine', 'geopandas', 'pyogrio', 'pyproj', 'shapely'):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = 'not installed'
    result['GDAL'] = rasterio.__gdal_version__
    return result


def rasterise_country(args, code, prepared=None):
    started = time.perf_counter()
    prepared = prepared if prepared is not None else load_country_inputs(args, code)
    grid, water = prepared['grid'], prepared['water']
    prepare(water)  # Prepared geometry accelerates repeated exact point membership.
    transform = Affine(*grid['transform'])
    outdir = args.output_root / code / 'raster'
    outdir.mkdir(parents=True, exist_ok=True)
    names = dict(mask=f'{code}_water_buffer_mask_100m.tif',
                 fraction=f'{code}_water_buffer_fraction_100m.tif',
                 water_resistance=f'{code}_water_buffer_water_resistance_100m.tif',
                 population_resistance=f'{code}_water_buffer_population_resistance_100m.tif',
                 report=f'{code}_water_buffer_raster.json')
    stats = dict(eligible_cells=0, majority_fraction_cells=0,
                 centre_eligible_below_half_fraction_cells=0,
                 centre_excluded_at_least_half_fraction_cells=0,
                 eligible_zero_sample_fraction_cells=0,
                 excluded_positive_sample_fraction_cells=0,
                 eligible_partial_sample_fraction_cells=0,
                 cells_with_positive_sample_fraction=0,
                 selected_10m_sample_centres=0,
                 processed_blocks=0, sampled_blocks=0, skipped_blocks=0)
    fraction_sum = 0.0
    eligible_fraction_sum = 0.0
    eligible_min, eligible_max = math.inf, -math.inf
    fraction_min, fraction_max = math.inf, -math.inf
    total_blocks = math.ceil(grid['width']/args.block_size) * math.ceil(grid['height']/args.block_size)
    print(f'{code}: rasterising water-buffer domain; {grid["width"]:,} x {grid["height"]:,} cells; '
          f'{total_blocks:,} blocks; strict 100 m centres and 10 m diagnostic samples', flush=True)
    last_progress = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f'{code}_raster_', dir=outdir) as directory:
        stage = Path(directory)
        with ExitStack() as stack:
            writers = {}
            for name in ('mask', 'fraction', 'water_resistance', 'population_resistance'):
                dtype, nodata = ('uint8', 255) if name == 'mask' else ('float32', NODATA)
                writers[name] = stack.enter_context(rasterio.open(stage/names[name], 'w', **raster_profile(grid, dtype, nodata)))
            for window in windows(grid['width'], grid['height'], args.block_size):
                rows, columns = int(window.height), int(window.width)
                x0, y1 = transform * (window.col_off, window.row_off)
                x1, y0 = transform * (window.col_off+window.width, window.row_off+window.height)
                if water.is_empty or not water.intersects(box(x0, y0, x1, y1)):
                    eligible = np.zeros((rows, columns), dtype=bool)
                    fraction = np.zeros((rows, columns), dtype='float32')
                    stats['skipped_blocks'] += 1
                else:
                    eligible, fraction = sample_block(water, transform, window)
                    stats['sampled_blocks'] += 1
                # Write every block: zero is meaningful, never leave unwritten NoData holes.
                writers['mask'].write(eligible.astype('uint8'), 1, window=window)
                writers['fraction'].write(fraction, 1, window=window)
                for name, multiplier in (('water_resistance', WATER_MULTIPLIER),
                                         ('population_resistance', POPULATION_MULTIPLIER)):
                    values = np.full((rows, columns), NODATA, dtype='float32')
                    values[eligible] = multiplier
                    writers[name].write(values, 1, window=window)
                majority = fraction >= 0.5
                stats['eligible_cells'] += int(eligible.sum())
                stats['majority_fraction_cells'] += int(majority.sum())
                stats['centre_eligible_below_half_fraction_cells'] += int((eligible & ~majority).sum())
                stats['centre_excluded_at_least_half_fraction_cells'] += int((~eligible & majority).sum())
                stats['eligible_zero_sample_fraction_cells'] += int((eligible & (fraction == 0)).sum())
                stats['excluded_positive_sample_fraction_cells'] += int((~eligible & (fraction > 0)).sum())
                stats['eligible_partial_sample_fraction_cells'] += int((eligible & (fraction > 0) & (fraction < 1)).sum())
                stats['cells_with_positive_sample_fraction'] += int((fraction > 0).sum())
                # Fractions are exact hundredth counts before float32 encoding.
                # Recover integer counts so reported sampled area is independent
                # of block summation order and floating-point storage roundoff.
                stats['selected_10m_sample_centres'] += int(np.rint(fraction*100).sum(dtype=np.float64))
                fraction_sum += float(fraction.sum(dtype=np.float64))
                fraction_min = min(fraction_min, float(fraction.min()))
                fraction_max = max(fraction_max, float(fraction.max()))
                if eligible.any():
                    values = fraction[eligible]
                    eligible_fraction_sum += float(values.sum(dtype=np.float64))
                    eligible_min = min(eligible_min, float(values.min()))
                    eligible_max = max(eligible_max, float(values.max()))
                stats['processed_blocks'] += 1
                if time.perf_counter()-last_progress >= 20:
                    print(f'{code}: {stats["processed_blocks"]:,}/{total_blocks:,} blocks; '
                          f'{stats["eligible_cells"]:,} eligible cells', flush=True)
                    last_progress = time.perf_counter()
            common = dict(country=code, stage='water_buffer_domain_rasterisation',
                          buffer_km=str(args.buffer_km), mask_rule='strict 100 m centre inside combined vector water',
                          boundary_centres='excluded', fraction_rule='100 strict 10 m centre samples per 100 m cell')
            for writer in writers.values():
                writer.update_tags(**common)
            writers['mask'].update_tags(description='1 = eligible extension; 0 = excluded; 255 = NoData (unused in complete output)')
            writers['fraction'].update_tags(description='Estimated mapped extension-water fraction across full expanded rectangle; zero is valid')
            writers['water_resistance'].update_tags(description='10 on eligible extension cells; excluded cells are NoData; no dilution with excluded land')
            writers['population_resistance'].update_tags(description='1 on eligible extension cells; excluded cells are NoData; no population data read')
        n = stats['eligible_cells']
        exact_area = float(water.area)
        estimated_area = stats['selected_10m_sample_centres'] * 100
        stats['majority_centre_disagreement_cells'] = (stats['centre_eligible_below_half_fraction_cells']
                                                     + stats['centre_excluded_at_least_half_fraction_cells'])
        stats['fraction'] = dict(minimum=fraction_min, maximum=fraction_max,
                                 mean_over_expanded_rectangle=fraction_sum/(grid['width']*grid['height']),
                                 eligible_minimum=eligible_min if n else None,
                                 eligible_maximum=eligible_max if n else None,
                                 eligible_mean=eligible_fraction_sum/n if n else None)
        recorded_ref = prepared['report'].get('inputs', {}).get('reference', {}).get('sha256')
        report = dict(schema_version=1, country=code, buffer_km=args.buffer_km,
                      created_utc=datetime.now(timezone.utc).isoformat(),
                      status=('no_mapped_water_selected' if water.is_empty else
                              'no_eligible_cell_centres' if not n else 'water_buffer_raster_domain_prepared'),
                      grid={**grid, 'rasterisation_status': 'Strict centre mask and 10 m sampled fractions created'},
                      original_reference_grid={key: prepared['reference'][key] for key in
                          ('crs', 'width', 'height', 'transform', 'factor', 'fine_width', 'fine_height', 'fine_transform')},
                      rules=dict(eligibility='Strict 100 m cell centre lies inside combined vector water; exact polygon-boundary centres excluded',
                                 fraction='Fraction of 100 nested 10 m cell centres strictly inside vector water; diagnostic only, not exact polygon area',
                                 majority_threshold=0.5, majority_rule_use='Diagnostic comparison only; does not change eligibility',
                                 water_multiplier=WATER_MULTIPLIER, population_multiplier=POPULATION_MULTIPLIER,
                                 partial_cell_multiplier='Full constant multiplier on eligible cells; excluded area never treated as neutral land',
                                 excluded_mask=0, mask_nodata=255,
                                 fraction_zero_meaning='No nested 10 m sample centres inside water; zero is valid',
                                 fraction_scope='Every cell in the expanded rectangle, including mask=0 cells; centre-excluded cells may have positive fraction',
                                 fraction_nodata=NODATA,
                                 resistance_nodata=NODATA, population_data_read=False,
                                 blue_teal='Both source display categories use the same combined-water geometry',
                                 existing_country_cells='Unmodified; later composition must give original valid cells priority'),
                      area_km2=dict(exact_vector_water=exact_area/1e6,
                                    estimated_water_from_10m_samples=estimated_area/1e6,
                                    eligible_100m_cell_footprint=n*10000/1e6,
                                    sampled_minus_vector=(estimated_area-exact_area)/1e6),
                      sampled_area_relative_difference_percent=(100*(estimated_area-exact_area)/exact_area if exact_area else None),
                      area_interpretation='Eligible cell footprint is not a measurement of physical water area',
                      diagnostics=stats, inputs=prepared['inputs'],
                      validation=dict(vector_crs_geometry_and_areas='checked',
                                      combined_water_within_strip_and_outside_country='checked to numerical area tolerance',
                                      current_reference_nesting_and_lattice_offsets='checked',
                                      current_reference_rectangle_containment='checked',
                                      reference_bytes_match_vector_report=(recorded_ref == prepared['inputs']['reference']['sha256'] if recorded_ref else None),
                                      reference_hash_policy='Informational only: paths and timestamp metadata may change without changing grid semantics',
                                      reference_history_limit='Vector report schema 2 lacks original reference dimensions snapshot; checks certify current consistency, not unchanged historical dimensions',
                                      vector_provenance_limit='Vector report schema 2 does not hash its GPKG; current layer geometry and areas are checked and current bytes recorded',
                                      original_valid_raster_overlap='Deferred to optional composition; no original mask read',
                                      routing_segments='Not validated; centres in water do not guarantee connecting segments stay in permitted geometry'),
                      remaining_layers=['slope', 'protected_areas', 'motorways', 'railways', 'pipelines'],
                      integration_status='Not integrated; remaining factors and missing-data policy must be resolved first',
                      processing=dict(block_size_100m=args.block_size, processing_seconds=time.perf_counter()-started,
                                      dependencies=dependency_versions()),
                      outputs={name: str((outdir/filename).resolve()) for name, filename in names.items()})
        (stage/names['report']).write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        # Stage all files successfully before touching previous results. The report
        # is committed last; this is not a multi-file transactional filesystem.
        for name in ('mask', 'fraction', 'water_resistance', 'population_resistance', 'report'):
            (stage/names[name]).replace(outdir/names[name])
    print(f'{code}: {n:,} eligible 100 m centres; exact vector water {exact_area/1e6:,.3f} km²; '
          f'10 m sample estimate {estimated_area/1e6:,.3f} km²; {outdir/names["report"]}', flush=True)
    return report


def main(argv=None):
    parser = build_parser()
    args = resolve_paths(parser.parse_args(argv))
    print(f'Water-buffer raster domain: countries {", ".join(args.countries)}; '
          f'buffer {args.buffer_km:g} km; script {Path(__file__).resolve()}', flush=True)
    missing = [str(path) for code in args.countries for path in input_paths(args, code).values() if not path.is_file()]
    if missing:
        parser.error('Required input files are missing:\n  '+'\n  '.join(missing))
    for code in args.countries:
        rasterise_country(args, code)


if __name__ == '__main__':
    main()
