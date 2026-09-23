#!/usr/bin/env python3
"""Multiply seven 100 m water-buffer factors, preserving missing data.

Install beside rasterise_water_buffer_protected_area.py, which supplies shared
domain validation. Reads only buffer outputs; no raw EMODnet, OSM or WDPA files
are needed. Original country rasters are untouched. No resampling, imputation,
additional area weighting, or multiplier cap is applied during integration.

Use --slope-root to select updated slope outputs in a separate scenario folder;
all other factors and the original domain still come from --buffer-root.
Finite-only slope reports are supported with their modelling assumptions
recorded explicitly. No raw source data or rerasterisation is needed.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import tempfile
import time

import numpy as np
from pyproj import CRS
import rasterio

import rasterise_water_buffer_protected_area as common

NODATA = common.NODATA
LAYER_RANGES = {'population': (1.0, 1.0), 'water': (10.0, 10.0),
                'slope': (1.0, 20.0), 'protected_area': (1.0, 30.0),
                'motorways': (1.0, 3.0), 'railways': (1.0, 3.0), 'pipelines': (0.25, 1.0)}
MISSING_BITS = {name: 1 << i for i, name in enumerate(LAYER_RANGES)}
NETWORK_METHOD = '10m_all_touched_exact_water_area_v1'


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', nargs='+', choices=common.COUNTRIES, default=list(common.COUNTRIES))
    parser.add_argument('--pipeline-dir', type=Path, default=common.PIPELINE_DIR)
    parser.add_argument('--buffer-root', type=Path, help='scenario root containing COUNTRY/domain and existing COUNTRY/raster factors')
    parser.add_argument('--slope-root', type=Path,
                        help='root containing COUNTRY/raster slope files and report; defaults to --buffer-root')
    parser.add_argument('--output-root', type=Path, help='scenario root for integrated COUNTRY/raster outputs')
    parser.add_argument('--buffer-km', type=common.positive_float, default=5.0)
    parser.add_argument('--block-size', type=common.block_size, default=256)
    return parser


def resolve_paths(args):
    args = common.resolve_paths(args)
    args.slope_root = (args.slope_root or args.buffer_root).expanduser().resolve()
    return args


def input_paths(args, code):
    folder = args.buffer_root/code/'raster'
    slope_folder = (getattr(args, 'slope_root', None) or args.buffer_root)/code/'raster'
    layers = {name: folder/f'{code}_water_buffer_{name}_resistance_100m.tif' for name in LAYER_RANGES}
    layers['slope'] = slope_folder/f'{code}_water_buffer_slope_resistance_100m.tif'
    reports = dict(slope=slope_folder/f'{code}_water_buffer_slope.json',
                   protected_area=folder/f'{code}_water_buffer_protected_area_resistance.json',
                   networks=folder/f'{code}_water_buffer_network_resistance.json')
    return layers, reports, slope_folder/f'{code}_water_buffer_slope_degrees_100m.tif'


def slope_diagnostic_paths(report, slope_folder, code):
    """Resolve local companions, never follow absolute paths from another host."""
    outputs = report.get('outputs', {})
    finite_only = report.get('elevation_policy') == 'finite-only'
    paths = {}
    for name, suffix in [('valid_mask', 'valid_mask'), ('gap_reason', 'gap_reason')]:
        if finite_only or name in outputs or (name == 'gap_reason' and 'gap_diagnostics' in report):
            paths[f'slope_{name}'] = slope_folder/f'{code}_water_buffer_slope_{suffix}_100m.tif'
    return paths


def validate_dataset(source, grid, label, diagnostic=False):
    dtype, nodata = ('uint8', 255) if diagnostic else ('float32', NODATA)
    if (source.count != 1 or source.crs is None or CRS.from_user_input(source.crs) != common.TARGET_CRS
            or (source.width, source.height) != (grid['width'], grid['height'])
            or source.dtypes != (dtype,) or source.nodata != nodata
            or source.scales != (1.0,) or source.offsets != (0.0,)
            or not np.allclose(list(source.transform)[:6], grid['transform'], atol=1e-8, rtol=0)):
        raise ValueError(f'{label}: expected aligned, unscaled {dtype} EPSG:3035 raster with NoData={nodata}; no resampling is performed')


def require_hash(recorded, current, label):
    if recorded.get('sha256') != current['sha256']:
        raise ValueError(f'{label}: content hash differs; regenerate the affected buffer stage')


def checked_report(report, prepared, args, code, name):
    if (report.get('schema_version') != 1 or report.get('country') != code
            or isinstance(report.get('buffer_km'), bool)
            or report.get('buffer_km') != args.buffer_km):
        raise ValueError(f'{code}/{name}: incompatible country, schema or buffer distance')
    transform = common.validate_grid(report['grid'], name)
    if (report['grid']['width'] != prepared['grid']['width']
            or report['grid']['height'] != prepared['grid']['height']
            or not np.allclose(list(transform)[:6], prepared['grid']['transform'], atol=1e-8, rtol=0)):
        raise ValueError(f'{code}/{name}: report grid differs')
    provenance = report.get('inputs', {})
    require_hash(provenance.get('mask', {}), prepared['inputs']['mask'], f'{code}/{name}/mask')
    domain_key = 'domain_report' if name == 'slope' else 'raster_report'
    require_hash(provenance.get(domain_key, {}), prepared['inputs']['raster_report'], f'{code}/{name}/raster-domain report')
    if name != 'slope':
        for key in ('gpkg', 'domain_report'):
            require_hash(provenance.get(key, {}), prepared['inputs'][key], f'{code}/{name}/{key}')


def preflight(args, code):
    print(f'{code}: checking domain and selected layer files, hashes and reports', flush=True)
    prepared = common.load_country_inputs(args, code)
    layers, report_paths, degrees = input_paths(args, code)
    paths = {**{f'layer_{k}': v for k, v in layers.items()},
             **{f'report_{k}': v for k, v in report_paths.items()}, 'slope_degrees': degrees}
    states = {key: common.file_state(path) for key, path in paths.items()}
    metadata = {key: common.file_metadata(path) for key, path in paths.items()}
    reports = {key: json.loads(path.read_text()) for key, path in report_paths.items()}
    diagnostic_paths = slope_diagnostic_paths(reports['slope'], degrees.parent, code)
    for key, path in diagnostic_paths.items():
        paths[key] = path
        states[key] = common.file_state(path)
        metadata[key] = common.file_metadata(path)
    for name, report in reports.items():
        checked_report(report, prepared, args, code, name)
    n = prepared['expected_eligible_cells']
    domain_rules = prepared['report']['rules']
    if domain_rules.get('water_multiplier') != 10 or domain_rules.get('population_multiplier') != 1:
        raise ValueError(f'{code}: expected constant water=10 and population=1')
    network = reports['networks']
    if network.get('report_type') != 'water_buffer_networks' or network.get('method') != NETWORK_METHOD:
        raise ValueError(f'{code}: incompatible network occupancy method')
    if network.get('diagnostics', {}).get('eligible_cells') != n:
        raise ValueError(f'{code}: network eligible count differs')
    expected_counts = {'population': n, 'water': n}
    expected_stats = {}
    for name, value in [('motorways', 3), ('railways', 3), ('pipelines', 0.25)]:
        layer = network.get('layers', {}).get(name, {})
        if layer.get('feature_multiplier') != value or layer.get('statistics', {}).get('count') != n:
            raise ValueError(f'{code}/{name}: network report factor/count differs')
        require_hash(layer.get('raster_signature', {}), metadata[f'layer_{name}'], f'{code}/{name}/output')
        expected_counts[name] = n
        expected_stats[name] = layer['statistics']
    protection = reports['protected_area']
    rules = protection.get('rules', {})
    if (rules.get('background') != 1 or rules.get('IUCN_II') != 30 or rules.get('other_protected_categories') != 10
            or rules.get('formula') != '1 + 9 * A_any / A_water + 20 * A_II / A_water'
            or protection.get('diagnostics', {}).get('eligible_cells') != n
            or protection.get('statistics', {}).get('count') != n):
        raise ValueError(f'{code}: incompatible protected-area rules or counts')
    require_hash(protection.get('raster_signature', {}), metadata['layer_protected_area'], f'{code}/protected_area/output')
    expected_counts['protected_area'] = n
    expected_stats['protected_area'] = protection['statistics']
    slope = reports['slope']
    policy = slope.get('elevation_policy')
    compatible_type = ((slope.get('report_type') == 'water_buffer_slope' and policy in (None, 'negative-only'))
                       or (slope.get('report_type') == 'water_buffer_slope_sensitivity' and policy == 'finite-only'))
    valid = slope.get('coverage', {}).get('valid_slope_cells')
    if (not compatible_type
            or slope.get('rules', {}).get('multiplier_formula') != '1 + 19 * slope_degrees / 90'
            or slope.get('coverage', {}).get('eligible_cells') != n
            or isinstance(valid, bool) or not isinstance(valid, int) or not 0 <= valid <= n
            or slope.get('coverage', {}).get('missing_slope_cells') != n-valid):
        raise ValueError(f'{code}: incompatible slope rules or coverage counts')
    if slope['statistics']['slope_resistance'].get('count') != valid:
        raise ValueError(f'{code}: slope statistics count differs from coverage')
    slope_model = dict(elevation_policy=policy or 'legacy-unspecified',
                       upstream_report_type=slope['report_type'],
                       upstream_production_integration_eligible=slope.get('production_integration_eligible'),
                       upstream_integration_status=slope.get('integration_status'),
                       selection='Use the slope dataset selected for this integration; retain its source assumptions',
                       source_screen=slope.get('rules', {}).get('source_screen'),
                       nonnegative_interpretation=slope.get('rules', {}).get('nonnegative_interpretation'),
                       multiplier_assumption=slope.get('rules', {}).get('multiplier_assumption'),
                       bathymetry_origin_verified=slope.get('coverage', {}).get('bathymetry_origin_verified', False),
                       valid_cells_requiring_nonnegative_support=slope.get('coverage', {}).get('valid_slope_cells_requiring_nonnegative_support'))
    # The old producer's eligibility flag remains provenance. It does not block
    # the explicitly selected finite-only scenario or certify its source origin.
    if policy == 'finite-only' and slope['coverage'].get('centre_rejected_source_cells') != 0:
        raise ValueError(f'{code}: finite-only report contains sign-based exclusions')
    expected_counts['slope'] = valid
    expected_stats['slope'] = slope['statistics']['slope_resistance']
    for name, path in {**layers, 'slope_degrees': degrees}.items():
        with rasterio.open(path) as source:
            validate_dataset(source, prepared['grid'], f'{code}/{name}')
    for name, path in diagnostic_paths.items():
        with rasterio.open(path) as source:
            validate_dataset(source, prepared['grid'], f'{code}/{name}', diagnostic=True)
    prepared.update(layer_paths=layers, report_paths=report_paths, slope_degrees=degrees,
                    slope_diagnostic_paths=diagnostic_paths, slope_model=slope_model,
                    layer_reports=reports, extra_paths=paths, extra_states=states,
                    extra_metadata=metadata, expected_counts=expected_counts, expected_stats=expected_stats)
    assert_unchanged(prepared, code)
    return prepared


def check_slope_diagnostics(arrays, included, usable, policy, code):
    """Verify the producer's usable domain; never use it to hide a discrepancy."""
    valid_mask = arrays.get('slope_valid_mask')
    if valid_mask is not None:
        if not np.isin(valid_mask, [0, 1]).all() or not np.array_equal(valid_mask == 1, usable):
            raise ValueError(f'{code}: slope validity mask differs from available slope pixels')
    gap = arrays.get('slope_gap_reason')
    if gap is not None:
        if (not np.isin(gap, [0, 1, 2, 3, 4, 5]).all()
                or not np.array_equal(gap != 0, included)
                or not np.array_equal(gap == 1, usable)):
            raise ValueError(f'{code}: slope gap reasons disagree with slope availability or domain')
        if policy == 'finite-only' and np.any(gap == 4):
            raise ValueError(f'{code}: finite-only slope has sign-based exclusions (gap code 4)')
        return np.bincount(gap.ravel(), minlength=256)
    return np.zeros(256, dtype=np.int64)


def assert_unchanged(prepared, code):
    common._assert_country_unchanged(prepared, code)
    if any(common.file_state(path) != prepared['extra_states'][key] for key, path in prepared['extra_paths'].items()):
        raise RuntimeError(f'{code}: input changed during integration; previous outputs preserved')


def valid_layer(data, included, name, bounds):
    values = np.asarray(data.data, dtype='float64')
    available = ~np.ma.getmaskarray(data)
    if np.any(available & ~np.isfinite(values)):
        raise ValueError(f'{name}: unmasked NaN or infinity; repair upstream')
    if np.any(available & ~included):
        raise ValueError(f'{name}: values outside the water-buffer mask')
    usable = included & available
    if np.any(usable & ((values < bounds[0]) | (values > bounds[1]))):
        raise ValueError(f'{name}: values outside allowed range {bounds}; zero is not neutral')
    return values, usable


def multiply_block(arrays, included):
    """Return product and missing-layer bit field; do not fill absent factors."""
    product = np.ones(included.shape, dtype='float64')
    missing = np.zeros(included.shape, dtype='uint8')
    usable_by_layer = {}
    for name, bounds in LAYER_RANGES.items():
        values, usable = valid_layer(arrays[name], included, name, bounds)
        usable_by_layer[name] = usable
        missing[included & ~usable] |= MISSING_BITS[name]
        product[usable] *= values[usable]
    complete = included & (missing == 0)
    if np.any(complete & (~np.isfinite(product) | (product < 2.5) | (product > 54000))):
        raise ValueError('Integrated product outside theoretical [2.5, 54000] range')
    output = np.full(included.shape, NODATA, dtype='float32')
    output[complete] = product[complete]
    missing[~included] = 255
    return output, missing, usable_by_layer


def empty_stats():
    return dict(count=0, minimum=math.inf, maximum=-math.inf, total=0.0)


def add_stats(stats, values):
    if values.size:
        stats['count'] += values.size
        stats['minimum'] = min(stats['minimum'], float(values.min()))
        stats['maximum'] = max(stats['maximum'], float(values.max()))
        stats['total'] += float(values.sum(dtype=np.float64))


def finish_stats(stats):
    n = int(stats['count'])
    return dict(count=n, minimum=stats['minimum'] if n else None,
                maximum=stats['maximum'] if n else None, mean=stats['total']/n if n else None)


def integrate_country(args, code, prepared=None):
    started = time.perf_counter()
    prepared = prepared or preflight(args, code)
    assert_unchanged(prepared, code)
    grid = prepared['grid']
    outdir = args.output_root/code/'raster'
    outdir.mkdir(parents=True, exist_ok=True)
    names = dict(integrated=f'{code}_water_buffer_integrated_resistance_100m.tif',
                 missing_layers=f'{code}_water_buffer_missing_layers_100m.tif',
                 report=f'{code}_water_buffer_integrated_resistance.json')
    counts = dict(eligible_cells=0, valid_integrated_cells=0, processed_blocks=0)
    layer_stats = {name: empty_stats() for name in LAYER_RANGES}
    integrated_stats = empty_stats()
    histogram = np.zeros(128, dtype=np.int64)
    slope_gap_histogram = np.zeros(256, dtype=np.int64)
    last_progress = time.perf_counter()
    print(f'{code}: multiplying seven water-buffer factors; missing values retained', flush=True)
    print(f'{code}: slope policy {prepared["slope_model"]["elevation_policy"]}; '
          f'source {prepared["layer_paths"]["slope"]}', flush=True)
    with tempfile.TemporaryDirectory(prefix=f'{code}_integrated_', dir=outdir) as directory:
        stage = Path(directory)
        with ExitStack() as stack:
            mask = stack.enter_context(rasterio.open(prepared['paths']['mask']))
            readers = {name: stack.enter_context(rasterio.open(path)) for name, path in prepared['layer_paths'].items()}
            slope_degrees = stack.enter_context(rasterio.open(prepared['slope_degrees']))
            diagnostic_readers = {name: stack.enter_context(rasterio.open(path))
                                  for name, path in prepared['slope_diagnostic_paths'].items()}
            writer = stack.enter_context(rasterio.open(stage/names['integrated'], 'w', **common.raster_profile(grid)))
            missing_profile = {**common.raster_profile(grid), 'dtype': 'uint8', 'nodata': 255, 'predictor': 1}
            missing_writer = stack.enter_context(rasterio.open(stage/names['missing_layers'], 'w', **missing_profile))
            for window in common.windows(grid['width'], grid['height'], args.block_size):
                included = common._checked_mask(mask.read(1, window=window), prepared['water'], prepared['transform'], window, code)
                # Scan all blocks, including excluded blocks, to detect stray
                # valid pixels outside the mask in any supposedly buffer-only input.
                arrays = {name: reader.read(1, window=window, masked=True) for name, reader in readers.items()}
                output, missing, usable = multiply_block(arrays, included)
                degrees, degree_valid = valid_layer(slope_degrees.read(1, window=window, masked=True), included,
                                                    'slope_degrees', (0, 90))
                if not np.array_equal(degree_valid, usable['slope']):
                    raise ValueError(f'{code}: slope degrees and resistance availability differ')
                if not np.allclose(arrays['slope'].data[degree_valid], 1+19*degrees[degree_valid]/90, atol=2e-6, rtol=2e-7):
                    raise ValueError(f'{code}: slope conversion differs from 1 + 19 * degrees / 90')
                slope_gap_histogram += check_slope_diagnostics(
                    {name: reader.read(1, window=window) for name, reader in diagnostic_readers.items()},
                    included, degree_valid, prepared['slope_model']['elevation_policy'], code)
                for name in LAYER_RANGES:
                    add_stats(layer_stats[name], arrays[name].data[usable[name]])
                complete = included & (missing == 0)
                add_stats(integrated_stats, output[complete])
                histogram += np.bincount(missing[included], minlength=128)
                counts['eligible_cells'] += int(included.sum())
                counts['valid_integrated_cells'] += int(complete.sum())
                counts['processed_blocks'] += 1
                writer.write(output, 1, window=window)
                missing_writer.write(missing, 1, window=window)
                if time.perf_counter()-last_progress >= 25:
                    print(f'{code}: {counts["eligible_cells"]:,} eligible cells checked; '
                          f'{counts["valid_integrated_cells"]:,} complete', flush=True)
                    last_progress = time.perf_counter()
            writer.update_tags(country=code, units='dimensionless', formula=' * '.join(LAYER_RANGES),
                               missing_policy='Any required factor missing => NoData; no imputation',
                               slope_elevation_policy=prepared['slope_model']['elevation_policy'],
                               slope_report_sha256=prepared['extra_metadata']['report_slope']['sha256'],
                               slope_source_assumption=(prepared['slope_model']['nonnegative_interpretation']
                                                        or 'See upstream slope report'),
                               domain='Optional water buffer only; original country raster unchanged')
            missing_writer.update_tags(description='0=all seven factors present; bits identify missing factors; 255=outside domain',
                                       missing_bits=json.dumps(MISSING_BITS))
        n, valid = counts['eligible_cells'], counts['valid_integrated_cells']
        if n != prepared['expected_eligible_cells']:
            raise ValueError(f'{code}: eligible count differs from domain report')
        if 'slope_gap_reason' in prepared['slope_diagnostic_paths']:
            expected_gaps = prepared['layer_reports']['slope'].get('gap_diagnostics', {}).get('counts', {})
            if any(expected_gaps.get(str(k)) != int(slope_gap_histogram[k]) for k in (0, 1, 2, 3, 4, 5, 255)):
                raise ValueError(f'{code}: slope gap counts differ from upstream report')
        finished_layers = {name: finish_stats(s) for name, s in layer_stats.items()}
        for name, statistics in finished_layers.items():
            if statistics['count'] != prepared['expected_counts'][name]:
                raise ValueError(f'{code}/{name}: pixel count differs from upstream report')
            expected = prepared['expected_stats'].get(name)
            if expected is not None and statistics['count']:
                for key in ('minimum', 'maximum', 'mean'):
                    if not math.isclose(statistics[key], expected[key], rel_tol=2e-6, abs_tol=2e-6):
                        raise ValueError(f'{code}/{name}: {key} differs from upstream report')
        assert_unchanged(prepared, code)
        signatures = {}
        for name in ('integrated', 'missing_layers'):
            signatures[name] = common.file_metadata(stage/names[name])
            signatures[name]['path'] = str(outdir/names[name])
        layer_coverage = {name: dict(valid_cells=s['count'], missing_cells=n-s['count'],
                                     missing_percent=100*(n-s['count'])/n if n else None)
                          for name, s in finished_layers.items()}
        status = ('no_eligible_buffer_cells' if not n else 'no_complete_buffer_cells' if not valid else
                  'water_buffer_integrated' if valid == n else 'water_buffer_integrated_with_gaps')
        report = dict(schema_version=1, report_type='water_buffer_integration', country=code,
                      buffer_km=args.buffer_km, created_utc=datetime.now(timezone.utc).isoformat(), status=status,
                      grid=grid, inputs={**prepared['inputs'], **prepared['extra_metadata']},
                      slope_model=prepared['slope_model'],
                      rules=dict(formula=' * '.join(LAYER_RANGES), neutral=1, water=10, population=1,
                                 nodata=NODATA, eligibility=common.ELIGIBILITY,
                                 missing_data='Any required factor missing => NoData; never replace by 1 or 0',
                                 aggregation='Product of completed 100 m layer means; no further area weights',
                                 resampling='None', cap='None', theoretical_range=[2.5, 54000],
                                 original_country='Not read or modified; optional composition remains separate'),
                      diagnostics={**counts, 'missing_integrated_cells': n-valid,
                                   'valid_percent': 100*valid/n if n else None,
                                   'missing_percent': 100*(n-valid)/n if n else None},
                      statistics=finish_stats(integrated_stats), layer_statistics=finished_layers,
                      layer_coverage=layer_coverage,
                      missing_diagnostic=dict(bits=MISSING_BITS, complete=0, outside_nodata=255,
                          combinations=[dict(value=i, missing_layers=[name for name, bit in MISSING_BITS.items() if i & bit],
                                             cells=int(number), percent_of_eligible=100*int(number)/n if n else None)
                                        for i, number in enumerate(histogram) if number]),
                      validation=dict(mask_equals_strict_vector_centres=True,
                                      layer_reports_match_current_domain_hashes=True,
                                      protected_and_network_output_hashes_checked=True,
                                      slope_degrees_conversion_and_summary_checked=True,
                                      slope_valid_mask_checked='slope_valid_mask' in prepared['slope_diagnostic_paths'],
                                      slope_gap_reasons_and_counts_checked='slope_gap_reason' in prepared['slope_diagnostic_paths'],
                                      timestamp_policy='SHA-256 content comparison across runs; file state monitored during execution',
                                      scope='Numeric and provenance consistency; source completeness and routing feasibility not certified'),
                      limitations=['Legacy domain and slope reports do not contain output TIFF hashes; constants, exact mask centres, slope-degree conversion and report counts/statistics are checked instead. Current input hashes are recorded here.',
                                   'Product of area means differs in general from area mean of pointwise products.',
                                   'Bathymetry gaps remain NoData and may disconnect routes; missing cells are not proof of a physical barrier.',
                                   'Finite-only slopes, when selected, accept either elevation sign; numeric validity alone does not establish seabed or intertidal provenance.',
                                   'Network completeness and physical offshore applicability of transferred penalties are not independently verified.',
                                   'Centre eligibility does not guarantee routing edges stay within permitted water.',
                                   'Per-file atomic replacement, report last; not a multi-file transaction. Run one writer per country.'],
                      processing=dict(block_size_100m=args.block_size, processing_seconds=time.perf_counter()-started,
                                      dependencies=common.dependency_versions()),
                      output_signatures=signatures,
                      outputs={name: str(outdir/filename) for name, filename in names.items()})
        (stage/names['report']).write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        for filename in names.values():
            (stage/filename).replace(outdir/filename)
    print(f'{code}: {valid:,}/{n:,} cells integrated; {n-valid:,} remain NoData; {outdir/names["report"]}', flush=True)
    return report


def main(argv=None):
    args = resolve_paths(build_parser().parse_args(argv))
    # Reject missing files for any requested country before publishing the first.
    for code in args.countries:
        layers, reports, degrees = input_paths(args, code)
        for path in [*common.input_paths(args, code).values(), *layers.values(), *reports.values(), degrees]:
            if not path.is_file():
                raise FileNotFoundError(path)
        slope_report = json.loads(reports['slope'].read_text())
        for path in slope_diagnostic_paths(slope_report, degrees.parent, code).values():
            if not path.is_file():
                raise FileNotFoundError(path)
    for code in args.countries:
        integrate_country(args, code)


if __name__ == '__main__':
    main()
