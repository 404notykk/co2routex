#!/usr/bin/env python3
"""Multiply seven aligned 100 m resistance factors for NL, DE and NO.

One is neutral; existing pipelines may reduce the product below one. Every
factor must be available for an output cell: any layer NoData propagates.
Legacy zero-background inputs are rejected, not reinterpreted. Multiply the
already aggregated 100 m factors without resampling, extra weights or a cap.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from time import perf_counter

import numpy as np
import rasterio
from affine import Affine

import prepare_reference_grid as reference

COUNTRIES = ('NL', 'DE', 'NO')
BLOCK_SIZE = 256  # Number of 100 m cells along each processing block edge.
NODATA = -9999.0
METHOD_VERSION = 2  # PRODUCT replaces the former additive integration.
NEUTRAL = 1.0
LAYER_PATHS = {
    'motorways': 'intermediate/existing_motorways/{country}_motorways_resistance_100m.tif',
    'pipelines': 'intermediate/existing_pipelines/{country}_pipelines_resistance_100m.tif',
    'railways': 'intermediate/existing_railways/{country}_railways_resistance_100m.tif',
    'water': 'intermediate/water/{country}_water_resistance_100m.tif',
    'slope': 'intermediate/slope/{country}_slope_resistance_100m.tif',
    'protected_areas': 'intermediate/protected_areas/{country}_protected_area_resistance_100m.tif',
    'population': 'intermediate/population/{country}_population_resistance_100m.tif',
}
LAYER_RANGES = {
    'motorways': (1.0, 3.0), 'pipelines': (0.25, 1.0), 'railways': (1.0, 3.0),
    'water': (1.0, 10.0), 'slope': (1.0, 20.0), 'protected_areas': (1.0, 30.0),
    'population': (1.0, 36.0),
}
POPULATION_CLASSES = (1, 4, 9, 16, 25, 36)
SIGNATURE_POLICY = ('Filename and size must match; timestamps must match exactly, or '
                    'within the same integer second when one has whole-second precision')


def input_paths(code):
    return {name: reference.PIPELINE_DIR / pattern.format(country=code)
            for name, pattern in LAYER_PATHS.items()}


def load_grid(code):
    grid = json.loads((reference.REFERENCE_DIR / f'{code}_reference_grid.json').read_text())
    if grid.get('schema_version') != 1 or grid.get('country') != code:
        raise ValueError(f'{code}: unsupported or mismatched reference JSON')
    for label, path in [('population', reference.POPULATION_PATH),
                        ('boundary', reference.boundary_path(code))]:
        if not signatures_match(grid[f'{label}_signature'], reference.fingerprint(path)):
            raise ValueError(f'{code}: {label} file metadata differs from the reference JSON: {path}. '
                             'Check the selected source files; this comparison does not establish '
                             'whether geometry or raster content changed.')
    return grid


def validate_dataset(dataset, grid, label, is_mask=False):
    width, height = grid['width'], grid['height']
    if (not isinstance(width, int) or not isinstance(height, int)
            or width <= 0 or height <= 0):
        raise ValueError('Reference dimensions must be positive integers')
    transform = Affine(*grid['transform'])
    expected_crs = rasterio.crs.CRS.from_epsg(3035)
    if rasterio.crs.CRS.from_string(grid['crs']) != expected_crs:
        raise ValueError('Reference CRS must be EPSG:3035')
    if not np.allclose([transform.a, transform.b, transform.d, transform.e],
                       [100, 0, 0, -100], rtol=0, atol=1e-8):
        raise ValueError('Reference must have north-up 100 m cells')
    if dataset.count != 1 or dataset.crs != expected_crs:
        raise ValueError(f'{label}: expected one band in EPSG:3035')
    if (dataset.width, dataset.height) != (width, height):
        raise ValueError(f'{label}: raster dimensions differ from the reference')
    if not np.allclose(list(dataset.transform)[:6], list(transform)[:6], rtol=0, atol=1e-7):
        raise ValueError(f'{label}: grid origin/resolution differs from reference; resampling is not performed')
    if dataset.scales != (1.0,) or dataset.offsets != (0.0,):
        raise ValueError(f'{label}: scale/offset metadata must be applied before integration')
    if np.dtype(dataset.dtypes[0]).kind not in 'uif':
        raise ValueError(f'{label}: expected a real numeric raster')
    if is_mask and dataset.nodata != 0:
        raise ValueError('Population-valid mask must declare NoData=0')
    if not is_mask:
        if dataset.nodata == 0:
            raise ValueError(f'{label}: NoData=0 is incompatible with the current resistance outputs; '
                             'regenerate the layer with neutral=1 and NoData=-9999')
        tags = dataset.tags()
        for field in ('background', 'background_multiplier', 'non_water_resistance'):
            if field in tags and float(tags[field]) != NEUTRAL:
                raise ValueError(f'{label}: {field} must be 1; regenerate this legacy layer')
        if label == 'water' and tags.get('source_missing') == 'zero contribution':
            raise ValueError('water: legacy zero-contribution raster; rerun the updated water script')
    return transform


def signatures_match(recorded, current):
    """Allow transfer-induced whole-second precision; retain original metadata."""
    def normalise(entries):
        return sorted((Path(item.get('name', item.get('path', ''))).name,
                       int(item['size']), int(item['mtime_ns'])) for item in entries)
    try:
        left, right = normalise(recorded), normalise(current)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return False
    if not left or len(left) != len(right):
        return False
    for (name, size, stamp), (other_name, other_size, other_stamp) in zip(left, right):
        if not name or size < 0 or (name, size) != (other_name, other_size):
            return False
        if stamp == other_stamp:
            continue
        if (stamp // 1_000_000_000 != other_stamp // 1_000_000_000
                or (stamp % 1_000_000_000 != 0 and other_stamp % 1_000_000_000 != 0)):
            return False
    return True


def validate_layer_report(code, key, path, grid, mask_path):
    """Check available producer metadata; missing reports do not certify history."""
    report_path = path.with_name(path.name.replace('_100m.tif', '.json'))
    if not report_path.is_file():
        return dict(status='not_available', path=str(report_path),
                    output_signature_checked=False, mask_signature_checked=False)
    record = json.loads(report_path.read_text())
    label = f'{code}/{key} report'
    if not isinstance(record, dict) or record.get('country') != code:
        raise ValueError(f'{label}: missing or mismatched country')
    reported_outputs = record.get('outputs', [record.get('output')])
    if path.name not in reported_outputs:
        raise ValueError(f'{label}: does not identify the expected raster {path.name}')
    recorded_grid = record.get('grid')
    if not isinstance(recorded_grid, dict):
        raise ValueError(f'{label}: missing reference grid')
    for field in ('country', 'crs', 'width', 'height', 'population_valid_mask', 'valid_population_cells'):
        if recorded_grid.get(field) != grid.get(field):
            raise ValueError(f'{label}: {field} differs from the current reference; regenerate the layer')
    if not np.allclose(recorded_grid['transform'], grid['transform'], rtol=0, atol=1e-7):
        raise ValueError(f'{label}: transform differs from the current reference')
    for field in ('population_signature', 'boundary_signature'):
        if field in grid and not signatures_match(recorded_grid.get(field, []), grid[field]):
            raise ValueError(f'{label}: {field} differs from the reference; regenerate the layer')
    cache = record.get('cache_key', {})
    metadata = record.get('metadata', {})
    for container in (record, cache, metadata):
        for field in ('background', 'background_multiplier', 'non_water_resistance'):
            if field in container and float(container[field]) != NEUTRAL:
                raise ValueError(f'{label}: {field} must be 1; regenerate this legacy layer')
    # Network cache.mask is a description; its fingerprint is in cache.inputs.mask.
    # Protected-area reports instead store the fingerprint directly in cache.mask.
    mask_signature = record.get('mask_signature')
    if mask_signature is None:
        mask_signature = cache.get('inputs', {}).get('mask')
    if mask_signature is None and not isinstance(cache.get('mask'), str):
        mask_signature = cache.get('mask')
    if mask_signature is not None and not isinstance(mask_signature, list):
        raise ValueError(f'{label}: malformed population mask signature; expected a list of file metadata')
    if mask_signature is not None and not signatures_match(mask_signature, reference.fingerprint(mask_path)):
        raise ValueError(f'{label}: population mask file metadata differs from {mask_path}; '
                         'compare the recorded and current mask before deciding whether to regenerate the layer')
    output_signature = record.get('output_signature', record.get('raster_signature'))
    if output_signature is not None and not signatures_match(output_signature, reference.fingerprint(path)):
        raise ValueError(f'{label}: raster signature differs; the TIFF and report do not match')
    return dict(status='available_fields_checked', path=str(report_path),
                signature=reference.fingerprint(report_path),
                producer_method_version=record.get('method_version', cache.get('version')),
                producer_method=record.get('method'),
                producer_missing_policy=record.get('missing_policy', record.get('source_missing_policy')),
                mask_signature_checked=mask_signature is not None,
                output_signature_checked=output_signature is not None)


def preflight(code, grid):
    paths = input_paths(code)
    if len(paths) != 7 or len({p.resolve() for p in paths.values()}) != 7:
        raise ValueError('Seven distinct resistance-layer files are required')
    mask_path = reference.REFERENCE_DIR / grid['population_valid_mask']
    with rasterio.open(mask_path) as mask:
        validate_dataset(mask, grid, 'population-valid mask', is_mask=True)
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f'{code}/{name}: {path}')
        with rasterio.open(path) as src:
            validate_dataset(src, grid, name)
        validate_layer_report(code, name, path, grid, mask_path)
    return paths


def new_stats():
    return dict(valid_cells=0, missing_cells=0, neutral_cells=0,
                discounted_cells=0, penalised_cells=0,
                minimum=None, maximum=None, value_sum=0.0)


def update_stats(stats, values):
    if not values.size:
        return
    low, high = float(values.min()), float(values.max())
    stats['minimum'] = low if stats['minimum'] is None else min(stats['minimum'], low)
    stats['maximum'] = high if stats['maximum'] is None else max(stats['maximum'], high)
    stats['valid_cells'] += int(values.size)
    stats['neutral_cells'] += int(np.count_nonzero(values == NEUTRAL))
    stats['discounted_cells'] += int(np.count_nonzero(values < NEUTRAL))
    stats['penalised_cells'] += int(np.count_nonzero(values > NEUTRAL))
    stats['value_sum'] += float(values.sum(dtype='float64'))


def validate_values(code, key, values):
    if not values.size:
        return
    if np.any(~np.isfinite(values)) or np.any(values <= 0):
        raise ValueError(f'{code}/{key}: unmasked zero, negative or nonfinite multiplier; '
                         'use current neutral=1 layers, not legacy zero-background contributions')
    low, high = LAYER_RANGES[key]
    tolerance = 1e-6 * high  # Float32 rounding at the documented endpoints only.
    if np.any(values < low-tolerance) or np.any(values > high+tolerance):
        raise ValueError(f'{code}/{key}: multiplier outside expected range {low:g}..{high:g}; '
                         'check the producer and input file')
    if key == 'population' and not np.all(np.isin(values, POPULATION_CLASSES)):
        raise ValueError(f'{code}/population: expected discrete multipliers {POPULATION_CLASSES}')


def process_country(code, grid=None, block_size=BLOCK_SIZE):
    started = perf_counter()
    if block_size <= 0:
        raise ValueError('Block size must be positive')
    grid = load_grid(code) if grid is None else grid
    paths = preflight(code, grid)
    width, height = grid['width'], grid['height']
    mask_path = reference.REFERENCE_DIR / grid['population_valid_mask']
    folder = reference.PIPELINE_DIR / 'spatial_cost_resistance'
    name = f'spatial_resistance_{code}.tif'
    report_name = f'spatial_resistance_{code}.json'
    layer_stats = {key: new_stats() for key in paths}
    result_stats = new_stats()
    count_histogram = np.zeros(8, dtype='int64')
    included_count = 0
    read_blocks = skipped_blocks = 0
    timings = dict(load_inputs=0.0, mask_read=0.0, layer_read=0.0,
                   validation_and_product=0.0, write=0.0)
    with ExitStack() as stack:
        mask = stack.enter_context(rasterio.open(mask_path))
        transform = validate_dataset(mask, grid, 'population-valid mask', is_mask=True)
        readers = {key: stack.enter_context(rasterio.open(path)) for key, path in paths.items()}
        for key, src in readers.items():
            validate_dataset(src, grid, key)
        sources = {key: dict(path=str(path), signature=reference.fingerprint(path),
                             nodata=repr(readers[key].nodata), expected_range=list(LAYER_RANGES[key]),
                             producer_report=validate_layer_report(code, key, path, grid, mask_path))
                   for key, path in paths.items()}
        timings['load_inputs'] = perf_counter() - started
        folder.mkdir(parents=True, exist_ok=True)
        total_blocks = ((width+block_size-1)//block_size)*((height+block_size-1)//block_size)
        print(f'{code}: multiplying seven layers on {width} x {height} cells at 100 m', flush=True)
        with tempfile.TemporaryDirectory(prefix=f'{code}_integrated_', dir=folder) as tmp:
            staged = Path(tmp)
            with rasterio.open(staged/name, 'w', **reference.profile(
                    width, height, transform, 'float32', NODATA)) as dst:
                for number, win in enumerate(reference.windows(width, height, block_size), 1):
                    stage_started = perf_counter()
                    raw_mask = mask.read(1, window=win)
                    if np.any((raw_mask != 0) & (raw_mask != 1)):
                        raise ValueError('Population-valid mask must contain only zero and one')
                    included = raw_mask == 1
                    included_count += int(included.sum())
                    timings['mask_read'] += perf_counter() - stage_started
                    products = np.ones(included.shape, dtype='float64')
                    available = np.zeros(included.shape, dtype='uint8')
                    if included.any():
                        read_blocks += 1
                        for key, src in readers.items():
                            stage_started = perf_counter()
                            raw = src.read(1, window=win, masked=True)
                            timings['layer_read'] += perf_counter() - stage_started
                            stage_started = perf_counter()
                            valid = included & ~np.ma.getmaskarray(raw)
                            selected = raw.data[valid]
                            validate_values(code, key, selected)
                            products[valid] *= selected
                            available[valid] += 1
                            update_stats(layer_stats[key], selected)
                            layer_stats[key]['missing_cells'] += int(np.count_nonzero(included & ~valid))
                            timings['validation_and_product'] += perf_counter() - stage_started
                    else:
                        skipped_blocks += 1
                    stage_started = perf_counter()
                    count_histogram += np.bincount(available[included], minlength=8)
                    valid_output = included & (available == len(paths))
                    if (np.any(~np.isfinite(products[valid_output]))
                            or np.any(products[valid_output] > np.finfo('float32').max)
                            or np.any(products[valid_output] <= 0)):
                        raise ValueError('Integrated resistance exceeds finite Float32 range')
                    output = np.full(included.shape, NODATA, dtype='float32')
                    output[valid_output] = products[valid_output]
                    update_stats(result_stats, output[valid_output])
                    timings['validation_and_product'] += perf_counter() - stage_started
                    stage_started = perf_counter()
                    dst.write(output, 1, window=win)
                    timings['write'] += perf_counter() - stage_started
                    if number == 1 or number % 100 == 0 or number == total_blocks:
                        print(f'  blocks {number}/{total_blocks}', flush=True)
                if included_count != grid['valid_population_cells'] or not included_count:
                    raise ValueError('Population-valid mask count differs from reference JSON')
                if not result_stats['valid_cells']:
                    raise ValueError('No cells have all seven valid layers; existing outputs are retained')
                dst.update_tags(integration='PRODUCT', method_version=METHOD_VERSION,
                                neutral_multiplier=NEUTRAL, input_layers=json.dumps(list(paths)),
                                missing_rule='any required layer missing yields NoData',
                                domain_mask=mask_path.name, units='dimensionless')
            timings['total_raster_stage'] = perf_counter() - started
            result_stats['missing_cells'] = included_count-result_stats['valid_cells']
            result_stats['mean'] = (result_stats['value_sum']/result_stats['valid_cells']
                                    if result_stats['valid_cells'] else None)
            for stats in layer_stats.values():
                stats['mean'] = stats['value_sum']/stats['valid_cells'] if stats['valid_cells'] else None
                stats['missing_percentage'] = 100.0*stats['missing_cells']/included_count
            report = dict(report_schema_version=2, method_version=METHOD_VERSION,
                          country=code, output=name, crs=grid['crs'], width=width, height=height,
                          transform=grid['transform'], grid=grid, inputs=sources,
                          mask_signature=reference.fingerprint(mask_path),
                          signature_comparison_policy=SIGNATURE_POLICY,
                          integration='PRODUCT', neutral_multiplier=NEUTRAL,
                          formula='motorways * pipelines * railways * water * slope * protected_areas * population',
                          aggregation_order='multiply separately completed 100 m factors; not the mean of subcell products',
                          weights='none; each supplied multiplier is applied once',
                          missing_rule='all seven factors required; any layer NoData propagates',
                          population_valid_cells=included_count,
                          outside_mask_cells=width*height-included_count,
                          complete_coverage_cells=int(count_histogram[7]),
                          partial_coverage_cells=int(count_histogram[1:7].sum()),
                          all_layers_missing_cells=int(count_histogram[0]),
                          dropped_missing_layer_cells=included_count-result_stats['valid_cells'],
                          dropped_missing_layer_percentage=100.0*(included_count-result_stats['valid_cells'])/included_count,
                          output_nodata_cells=width*height-result_stats['valid_cells'],
                          available_layer_count_histogram={str(i): int(n) for i, n in enumerate(count_histogram)},
                          layer_statistics=layer_stats, output_statistics=result_stats,
                          statistics_scope='Within population-valid mask; per-layer statistics use each layer valid cells; '
                                           'final statistics use saved Float32 products with all seven factors available',
                          processing_blocks=dict(total=total_blocks, layers_read=read_blocks,
                                                 skipped_empty=skipped_blocks),
                          timings_seconds=timings,
                          timing_scope='Per-country processing including its preflight through closing the temporary TIFF; '
                                       'excludes main command preflight, JSON writing and replacements. Write measures block calls.',
                          provenance_note='Available producer reports are checked; only reports with an output signature '
                                          'compare the TIFF to recorded size and mtime, allowing whole-second precision '
                                          'after file transfer. Signatures are metadata, not checksums.',
                          nodata=NODATA, block_size=block_size, resampling='none')
            (staged/report_name).write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
            (staged/name).replace(folder/name)
            (staged/report_name).replace(folder/report_name)
    print(f'Saved {folder/name}; {result_stats["valid_cells"]:,} valid cells; '
          f'{included_count-result_stats["valid_cells"]:,} excluded for missing factors; '
          f'mean multiplier {result_stats["mean"]:.4f}; {timings["total_raster_stage"]:.2f} s', flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', nargs='+', choices=COUNTRIES, default=COUNTRIES)
    parser.add_argument('--check-only', action='store_true',
                        help='Check files, grid metadata and available producer reports; no value scan or output writing')
    args = parser.parse_args()
    grids = {code: load_grid(code) for code in args.countries}
    # Validate every selected country's input files before producing any output.
    for code, grid in grids.items():
        preflight(code, grid)
        print(f'{code}: seven input files, reference alignment and available producer reports checked', flush=True)
    if args.check_only:
        print('Metadata checks complete. Raster values were not scanned; no outputs written.')
        return
    for code, grid in grids.items():
        process_country(code, grid)


if __name__ == '__main__':
    main()
