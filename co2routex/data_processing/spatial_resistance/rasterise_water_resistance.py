#!/usr/bin/env python3
"""Create water resistance on the population-aligned 100 m grid.

Copernicus LCFM LCM-10 MAP class 100 (permanent water) -> 10; classified non-water -> 1.
Nearest-neighbour projection to nested 10 m EPSG:3035 cells precedes classification.
The mean of each 10 x 10 block is 1 + 9 * water_fraction, between 1 and 10.
Missing source data propagate to NoData by default; --missing ignore uses observed area.

Source: Copernicus Land Monitoring Service, Land Cover 2020, 10 m, version 1.
https://doi.org/10.2909/602507b2-96c7-47bb-b79d-7ba25e97d0a9
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
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import Window, bounds

import prepare_reference_grid as reference

COUNTRIES = ('NL', 'DE', 'NO')
# Raw inputs relative to database/pipeline. List multiple files to combine coverage.
INPUT_FILES = {
    'NL': ('raw/water/LCM_10_NL.tif',),
    'DE': ('raw/water/LCM_10_DE.tif',),
    'NO': ('raw/water/LCM_10_2020_NO_A.tif',
           'raw/water/LCM_10_2020_NO_B.tif'),
}
RAW_TILE_DIR = reference.PIPELINE_DIR / 'raw' / 'water'
WATER_CLASS = 100
WATER_RESISTANCE = 10.0
NON_WATER_RESISTANCE = 1.0
METHOD_VERSION = 2
UNCLASSIFIABLE_CLASS = 254
SOURCE_NODATA_CLASS = 255
BLOCK_SIZE = 128  # 100 m cells per block edge; 1280 x 1280 fine cells.
FACTOR = 10
NODATA = -9999.0
SAVE_10M = False
SOURCE_DOI = 'https://doi.org/10.2909/602507b2-96c7-47bb-b79d-7ba25e97d0a9'
SIGNATURE_POLICY = ('Filename and size must match; timestamps must match exactly, or '
                    'within the same integer second when one has whole-second precision')


def find_sources(code, raw_tiles=False):
    if raw_tiles:
        paths = sorted(RAW_TILE_DIR.rglob('*_MAP.tif'))
        if not paths:
            raise FileNotFoundError(f'No *_MAP.tif files under {RAW_TILE_DIR}')
    else:
        paths = [Path(p) if Path(p).is_absolute() else reference.PIPELINE_DIR / p
                 for p in INPUT_FILES[code]]
    if not paths or len(set(p.resolve() for p in paths)) != len(paths):
        raise ValueError('Expected distinct input raster files')
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    return paths


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


def load_grid(code):
    grid = json.loads((reference.REFERENCE_DIR / f'{code}_reference_grid.json').read_text())
    if grid.get('schema_version') != 1 or grid.get('country') != code:
        raise ValueError('Unsupported or mismatched reference JSON')
    for label, path in [('population', reference.POPULATION_PATH),
                        ('boundary', reference.boundary_path(code))]:
        if not signatures_match(grid[f'{label}_signature'], reference.fingerprint(path)):
            raise ValueError(f'{code}: {label} file metadata differs from the reference JSON: {path}. '
                             'Check the selected source files; this comparison does not establish '
                             'whether geometry or raster content changed.')
    return grid


def validate_grid(mask, grid):
    transform = Affine(*grid['transform'])
    fine = Affine(*grid['fine_transform'])
    if (grid['factor'] != FACTOR or grid['fine_width'] != grid['width']*FACTOR
            or grid['fine_height'] != grid['height']*FACTOR):
        raise ValueError('Expected exactly nested 10 m and 100 m grids')
    if not np.allclose([transform.a, transform.b, transform.d, transform.e],
                       [100, 0, 0, -100], atol=1e-8, rtol=0):
        raise ValueError('Expected north-up 100 m reference grid')
    if not np.allclose(list(fine)[:6], list(transform*Affine.scale(1/FACTOR))[:6],
                       atol=1e-7, rtol=0):
        raise ValueError('Fine-grid alignment differs from reference')
    if (mask.crs != rasterio.crs.CRS.from_epsg(3035)
            or rasterio.crs.CRS.from_string(grid['crs']) != mask.crs
            or (mask.width, mask.height) != (grid['width'], grid['height'])
            or mask.count != 1 or mask.nodata != 0
            or not np.allclose(list(mask.transform)[:6], list(transform)[:6], atol=1e-7, rtol=0)):
        raise ValueError('Population-valid mask differs from reference grid')
    return transform, fine


def overlaps(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def combine_block(readers, win, active, local_bounds):
    """Combine valid observations; overlapping water/non-water disagreements fail."""
    shape = active.shape
    observed = np.zeros(shape, dtype=bool)
    water = np.zeros(shape, dtype=bool)
    unclassifiable = np.zeros(shape, dtype=bool)
    class_counts = {}
    for reader, source_bounds in readers:
        if not overlaps(source_bounds, local_bounds):
            continue
        raw = reader.read(1, window=win, masked=True)
        usable = active & ~np.ma.getmaskarray(raw)
        values = raw.data
        if np.any(usable & (~np.isfinite(values) | (values < 0) | (values > 255)
                            | (values != np.floor(values)))):
            raise ValueError('Expected integer LCM MAP class values in 0..255')
        # Product missing/unclassifiable codes never count as observed land.
        unclassifiable |= usable & (values == UNCLASSIFIABLE_CLASS)
        usable &= (values != UNCLASSIFIABLE_CLASS) & (values != SOURCE_NODATA_CLASS)
        selected_water = values == WATER_CLASS
        if np.any(observed & usable & (water != selected_water)):
            raise ValueError('Overlapping source files disagree on water/non-water classification. '
                             'Check input versions or select a consistent mosaic in INPUT_FILES.')
        new = usable & ~observed
        unique, counts = np.unique(values[new], return_counts=True)
        for value, count in zip(unique, counts):
            key = str(int(value))
            class_counts[key] = class_counts.get(key, 0) + int(count)
        water[new] = selected_water[new]
        observed |= usable
    return water, observed, unclassifiable & ~observed, class_counts


def aggregate_water(water, observed, included, missing='propagate'):
    """Return multipliers and classified 10 m cell counts for each 100 m cell."""
    if missing not in ('propagate', 'ignore'):
        raise ValueError('Missing policy must be propagate or ignore')
    h, w = included.shape
    count = observed.reshape(h, FACTOR, w, FACTOR).sum(axis=(1, 3))
    water_count = water.reshape(h, FACTOR, w, FACTOR).sum(axis=(1, 3))
    keep = included & ((count == FACTOR**2) if missing == 'propagate' else (count > 0))
    coarse = np.full((h, w), NODATA, dtype='float32')
    coarse[keep] = NON_WATER_RESISTANCE + (WATER_RESISTANCE-NON_WATER_RESISTANCE) * (
        water_count[keep] / count[keep])
    return coarse, keep, count


def process_country(code, sources, grid=None, block_size=BLOCK_SIZE, save_10m=SAVE_10M,
                    missing='propagate'):
    started = perf_counter()
    if block_size <= 0:
        raise ValueError('Block size must be positive')
    if missing not in ('propagate', 'ignore'):
        raise ValueError('Missing policy must be propagate or ignore')
    grid = load_grid(code) if grid is None else grid
    folder = reference.PIPELINE_DIR / 'intermediate' / 'water'
    mask_path = reference.REFERENCE_DIR / grid['population_valid_mask']
    name = f'{code}_water_resistance_100m.tif'
    fine_name = f'{code}_water_resistance_10m.tif'
    report_name = f'{code}_water_resistance.json'
    width, height = grid['width'], grid['height']
    population_count = valid_count = wet_coarse = missing_count = 0
    unknown_count = observed_count = water_count = 0
    complete = partial = absent = 0
    minimum, maximum, value_sum = float('inf'), float('-inf'), 0.0
    source_reports, class_counts = [], {}
    total = ((width+block_size-1)//block_size)*((height+block_size-1)//block_size)
    blocks = dict(total=total, source_read=0, skipped_empty=0, no_source_overlap=0)
    timings = dict(load_inputs=0.0, mask_read=0.0, source_read_and_mosaic=0.0,
                   aggregation_and_statistics=0.0, write=0.0)
    with ExitStack() as stack:
        stack.enter_context(rasterio.Env(GDAL_CACHEMAX=128*1024*1024))
        mask = stack.enter_context(rasterio.open(mask_path))
        transform, fine_transform = validate_grid(mask, grid)
        extent = bounds(Window(0, 0, width, height), transform)
        readers = []
        for path in sources:
            src = stack.enter_context(rasterio.open(path))
            if src.count != 1 or src.crs is None:
                raise ValueError(f'Expected single-band georeferenced MAP raster: {path}')
            if src.scales != (1.0,) or src.offsets != (0.0,):
                raise ValueError(f'Unexpected source scale/offset: {path}')
            source_bounds = transform_bounds(src.crs, reference.CRS, *src.bounds, densify_pts=41)
            if not overlaps(extent, source_bounds):
                continue
            vrt = stack.enter_context(WarpedVRT(src, crs=reference.CRS, transform=fine_transform,
                        width=width*FACTOR, height=height*FACTOR, dtype='float32',
                        nodata=NODATA, resampling=Resampling.nearest, tolerance=1e-9,
                        warp_mem_limit=32))
            readers.append((vrt, source_bounds))
            source_reports.append(dict(source=str(path), signature=reference.fingerprint(path),
                                       crs=str(src.crs), nodata=repr(src.nodata),
                                       dtype=src.dtypes[0], resolution=list(src.res),
                                       mask_flags=[flag.name for flag in src.mask_flag_enums[0]],
                                       width=src.width, height=src.height,
                                       transform=list(src.transform)[:6]))
        if not readers:
            raise ValueError(f'No source rasters overlap the reference grid for {code}')
        timings['load_inputs'] = perf_counter()-started
        folder.mkdir(parents=True, exist_ok=True)
        print(f'{code}: {len(readers)} source rasters; processing {width} x {height} at 100 m', flush=True)
        with tempfile.TemporaryDirectory(prefix=f'{code}_water_', dir=folder) as tmp:
            staged = Path(tmp)
            with ExitStack() as outputs:
                dst = outputs.enter_context(rasterio.open(staged/name, 'w', **reference.profile(
                    width, height, transform, 'float32', NODATA)))
                fine_dst = None
                if save_10m:
                    fine_dst = outputs.enter_context(rasterio.open(staged/fine_name, 'w', **reference.profile(
                        width*FACTOR, height*FACTOR, fine_transform, 'float32', NODATA)))
                for number, win in enumerate(reference.windows(width, height, block_size), 1):
                    tick = perf_counter()
                    raw_mask = mask.read(1, window=win)
                    if np.any((raw_mask != 0) & (raw_mask != 1)):
                        raise ValueError('Population-valid mask must contain only zero and one')
                    included = raw_mask == 1
                    population_count += int(included.sum())
                    timings['mask_read'] += perf_counter()-tick
                    h, w = included.shape
                    active = np.repeat(np.repeat(included, FACTOR, axis=0), FACTOR, axis=1)
                    fine_win = Window(win.col_off*FACTOR, win.row_off*FACTOR, w*FACTOR, h*FACTOR)
                    coarse = np.full((h, w), NODATA, dtype='float32')
                    fine = np.full(active.shape, NODATA, dtype='float32') if fine_dst else None
                    if included.any():
                        local_bounds = bounds(win, transform)
                        if any(overlaps(source_bounds, local_bounds) for _, source_bounds in readers):
                            blocks['source_read'] += 1
                        else:
                            blocks['no_source_overlap'] += 1
                        tick = perf_counter()
                        water, observed, unknown, counts = combine_block(
                            readers, fine_win, active, local_bounds)
                        timings['source_read_and_mosaic'] += perf_counter()-tick
                        tick = perf_counter()
                        coarse, keep, count = aggregate_water(water, observed, included, missing)
                        complete += int(np.count_nonzero(included & (count == FACTOR**2)))
                        partial += int(np.count_nonzero(included & (count > 0) & (count < FACTOR**2)))
                        absent += int(np.count_nonzero(included & (count == 0)))
                        water_count += int(water.sum())
                        observed_count += int(observed.sum())
                        missing_count += int(np.count_nonzero(active & ~observed))
                        unknown_count += int(unknown.sum())
                        for key, count_value in counts.items():
                            class_counts[key] = class_counts.get(key, 0)+count_value
                        valid_count += int(keep.sum())
                        if keep.any():
                            values = coarse[keep]
                            minimum = min(minimum, float(values.min()))
                            maximum = max(maximum, float(values.max()))
                            value_sum += float(values.sum(dtype='float64'))
                            wet_coarse += int(np.count_nonzero(values > NON_WATER_RESISTANCE))
                        if fine_dst:
                            fine[observed] = NON_WATER_RESISTANCE
                            fine[water] = WATER_RESISTANCE
                        timings['aggregation_and_statistics'] += perf_counter()-tick
                    else:
                        blocks['skipped_empty'] += 1
                    tick = perf_counter()
                    dst.write(coarse, 1, window=win)
                    if fine_dst:
                        fine_dst.write(fine, 1, window=fine_win)
                    timings['write'] += perf_counter()-tick
                    if number == 1 or number % 100 == 0 or number == total:
                        print(f'  blocks {number}/{total}', flush=True)
                if population_count != grid['valid_population_cells'] or not population_count:
                    raise ValueError('Mask valid-cell count differs from reference JSON')
                if not valid_count:
                    raise ValueError(f'No usable 100 m water cells for {code} with missing={missing}')
                tags = dict(method_version=METHOD_VERSION, background=NON_WATER_RESISTANCE,
                            water_class=WATER_CLASS, water_resistance=WATER_RESISTANCE,
                            non_water_resistance=NON_WATER_RESISTANCE, missing_policy=missing,
                            source_doi=SOURCE_DOI, resampling='nearest')
                dst.update_tags(**tags, formula='1 + 9 * water_fraction',
                                aggregation='MEAN of classified nested 10 m multipliers',
                                mask='100 m master mask; full nested-cell footprint')
                if fine_dst:
                    fine_dst.update_tags(**tags, mask='100 m master mask repeated to 10 m',
                                         source_missing='NoData',
                                         description='Classified source: water 10, non-water 1; missing -9999')
            timings['total_raster_stage'] = perf_counter()-started
            lost = population_count-valid_count
            coverage = dict(denominator='population-valid 100 m cells',
                            expected_source_cells_per_output_cell=FACTOR**2,
                            complete_cells=complete, partial_cells=partial, absent_cells=absent,
                            percentages=dict(complete=100*complete/population_count,
                                             partial=100*partial/population_count,
                                             absent=100*absent/population_count))
            report = dict(method_version=METHOD_VERSION, country=code, output=name,
                          fine_output=fine_name if save_10m else None,
                          grid=grid, sources=source_reports, source_doi=SOURCE_DOI,
                          mask_signature=reference.fingerprint(mask_path),
                          signature_comparison_policy=SIGNATURE_POLICY, water_class=WATER_CLASS,
                          water_resistance=WATER_RESISTANCE, non_water_resistance=NON_WATER_RESISTANCE,
                          background=NON_WATER_RESISTANCE, formula='1 + 9 * water_fraction',
                          factor=FACTOR, aggregation='MEAN', resampling='nearest',
                          missing_policy=missing,
                          source_missing_policy=(
                              'NoData if any of 100 nested cells is missing or unclassifiable'
                              if missing == 'propagate' else
                              'mean over observed nested cells; all-missing output is NoData'),
                          footprint_policy='Full 100 m cell; master mask repeated to 10 m, no polygon clipping',
                          mosaic_policy='first valid observation; reject water/non-water disagreement',
                          observed_source_class_counts=class_counts, source_coverage=coverage,
                          population_valid_cells=population_count, valid_cells=valid_count,
                          nodata_cells=width*height-valid_count,
                          missing_water_cells_within_population_mask=lost,
                          missing_water_percentage_within_population_mask=100*lost/population_count,
                          water_affected_100m_cells=wet_coarse,
                          water_affected_percentage=100*wet_coarse/valid_count,
                          water_10m_cells=water_count, observed_10m_cells=observed_count,
                          missing_source_10m_cells=missing_count,
                          unclassifiable_10m_cells=unknown_count,
                          missing_source_fraction=missing_count/(population_count*FACTOR**2),
                          multiplier_statistics=dict(min=minimum, max=maximum, mean=value_sum/valid_count),
                          minimum=minimum, maximum=maximum, nodata=NODATA, block_size=block_size,
                          processing_blocks=blocks, timings_seconds=timings,
                          timing_scope='Per-country processing through closing temporary TIFFs; '
                                       'excludes JSON writing and final file replacements')
            (staged/report_name).write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
            for output in [name]+([fine_name] if save_10m else [])+[report_name]:
                (staged/output).replace(folder/output)
    print(f'Saved {folder/name}; {valid_count:,} usable 100 m cells; '
          f'{lost:,} population-valid cells without sufficient water coverage '
          f'({lost/population_count:.2%}); {timings["total_raster_stage"]:.2f} s', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', nargs='+', choices=COUNTRIES, default=COUNTRIES)
    parser.add_argument('--save-10m', action='store_true', default=SAVE_10M)
    parser.add_argument('--raw-tiles', action='store_true', help='Read raw/water/**/*_MAP.tif instead of country mosaics')
    parser.add_argument('--missing', choices=('propagate', 'ignore'), default='propagate',
                        help='Require all 100 source cells (default), or average observed cells only')
    args = parser.parse_args()
    grids = {code: load_grid(code) for code in args.countries}
    sources = {code: find_sources(code, args.raw_tiles) for code in args.countries}
    for code, grid in grids.items():
        process_country(code, sources[code], grid, save_10m=args.save_10m, missing=args.missing)


if __name__ == '__main__':
    main()
