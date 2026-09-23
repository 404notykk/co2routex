#!/usr/bin/env python3
"""Decode EU-DEM slope, average degrees to the population grid, and assign resistance.

Source: European Commission – DG ENTR, 2012, EU-DEM Version 1.
Data funded under GMES preparatory action 2009 on Reference Data Access by the
European Commission, DG Enterprise and Industry.

Convert source values with degrees = degrees(acos(DN / 250)), average degrees
from 25 m to 100 m, then calculate resistance = 1 + 19 * mean_degrees / 90.
This degree-linear mapping is CO2RouteX's interpretation of the 1--20 endpoints
in Bogs et al., not a reproduction of the authors' released DN-based formula.

The default requires sixteen valid source cells per output cell.
Use --missing ignore to average available cells. All-missing cells remain NoData.
"""
import argparse
import json
from pathlib import Path
import tempfile
from time import perf_counter

import numpy as np
import rasterio
from affine import Affine
from rasterio.windows import Window
import prepare_reference_grid as reference

COUNTRIES = ('NL', 'DE', 'NO')
SOURCE = reference.PIPELINE_DIR / 'raw' / 'slope' / 'eudem_slop_3035_europe.tif'
BLOCK_SIZE = 256  # Number of 100 m cells on each processing block edge.
FACTOR = 4  # Four 25 m cells along each 100 m cell edge.
NODATA = -9999.0
MISSING_POLICY = 'propagate'
METHOD_VERSION = 1  # First explicit version; the existing numerical method is retained.
CONVERSION_SOURCE = 'https://ec.europa.eu/eurostat/documents/7116161/7172326/SPEC011-b140109-SLOP.pdf'
PUBLICATION = {
    'authors': 'Bogs, S.; Abdelshafy, A.; Walther, G.',
    'title': 'Planning minimum regret CO2 pipeline networks',
    'doi': 'https://doi.org/10.1080/24725854.2025.2602823',
    'endpoint_source': 'arXiv v1, Appendix B, Table 5: slope 0--90 degrees; multiplier 1--20',
    'table_url': 'https://arxiv.org/html/2502.12035v1#A2.T5',
    'released_code_url': 'https://zenodo.org/records/15829448',
    'interpretation': 'CO2RouteX averages decoded degrees and linearly interpolates the paper endpoints; '
                      'the table does not explicitly prescribe linear interpolation.',
}


def aggregate_slope(raw, included, missing=MISSING_POLICY):
    """Return degrees, resistance, output validity and valid source-cell counts.

    Decode before averaging. Source masks define missing values, not DN zero.
    Counts describe source coverage independently of the chosen missing policy.
    """
    if missing not in ('propagate', 'ignore'):
        raise ValueError('Unknown missing-data policy')
    h, w = included.shape
    if raw.shape != (h * FACTOR, w * FACTOR):
        raise ValueError('Slope block dimensions do not match reference block')
    relevant = np.repeat(np.repeat(included, FACTOR, axis=0), FACTOR, axis=1)
    valid = ~np.ma.getmaskarray(raw) & relevant
    values = np.asarray(raw.data, dtype='float64')
    if np.any(valid & (~np.isfinite(values) | (values < 0) | (values > 250))):
        raise ValueError('Unmasked slope DN outside 0..250 or nonfinite. Check source encoding '
                         'and NoData metadata; values are not clipped or silently discarded.')
    fine = np.zeros(raw.shape, dtype='float64')
    fine[valid] = np.degrees(np.arccos(values[valid] / 250.0))
    count = valid.reshape(h, FACTOR, w, FACTOR).sum(axis=(1, 3))
    total = fine.reshape(h, FACTOR, w, FACTOR).sum(axis=(1, 3))
    keep = included & ((count == FACTOR**2) if missing == 'propagate' else (count > 0))
    degrees = np.full((h, w), NODATA, dtype='float32')
    resistance = degrees.copy()
    mean = total[keep] / count[keep]
    degrees[keep] = mean
    resistance[keep] = 1 + 19 * mean / 90
    return degrees, resistance, keep, count


def load_grid(code):
    grid = json.loads((reference.REFERENCE_DIR / f'{code}_reference_grid.json').read_text())
    if grid.get('schema_version') != 1 or grid.get('country') != code:
        raise ValueError('Unsupported or mismatched reference JSON')
    for label, path in [('population', reference.POPULATION_PATH),
                        ('boundary', reference.boundary_path(code))]:
        if grid[f'{label}_signature'] != reference.fingerprint(path):
            raise ValueError(f'{code}: {label} changed; regenerate the reference grid')
    return grid


def validate_alignment(src, mask, grid):
    transform = Affine(*grid['transform'])
    crs = rasterio.crs.CRS.from_epsg(3035)
    if src.crs != crs or mask.crs != crs or rasterio.crs.CRS.from_string(grid['crs']) != crs:
        raise ValueError('Slope, mask and reference must use EPSG:3035')
    if src.count != 1 or mask.count != 1:
        raise ValueError('Expected single-band rasters')
    if not np.allclose([src.transform.a, src.transform.b, src.transform.d, src.transform.e],
                       [25, 0, 0, -25], atol=1e-8, rtol=0):
        raise ValueError('Expected north-up 25 m slope raster')
    if not np.allclose([transform.a, transform.b, transform.d, transform.e],
                       [100, 0, 0, -100], atol=1e-8, rtol=0):
        raise ValueError('Expected north-up 100 m reference grid')
    if ((mask.width, mask.height) != (grid['width'], grid['height']) or mask.nodata != 0
            or not np.allclose(list(mask.transform)[:6], list(transform)[:6], atol=1e-7, rtol=0)):
        raise ValueError('Population-valid mask differs from reference grid')
    col, row = (~src.transform) * (transform.c, transform.f)
    if not np.allclose([col, row], np.round([col, row]), atol=1e-6, rtol=0):
        raise ValueError('25 m source cells do not nest in the 100 m grid. '
                         'An explicit resampling decision is required; no automatic shift applied.')
    return transform, int(round(col)), int(round(row))


def process_country(code, grid=None, missing=MISSING_POLICY, block_size=BLOCK_SIZE):
    started = perf_counter()
    if block_size <= 0 or missing not in ('propagate', 'ignore'):
        raise ValueError('Invalid block size or missing-data policy')
    grid = load_grid(code) if grid is None else grid
    mask_path = reference.REFERENCE_DIR / grid['population_valid_mask']
    folder = reference.PIPELINE_DIR / 'intermediate' / 'slope'
    names = [f'{code}_slope_degrees_100m.tif', f'{code}_slope_resistance_100m.tif']
    report_name = f'{code}_slope_resistance.json'
    width, height = grid['width'], grid['height']
    included_count = valid_count = 0
    low, high = float('inf'), float('-inf')
    resistance_low, resistance_high = float('inf'), float('-inf')
    degrees_sum = resistance_sum = 0.0
    complete_count = partial_count = absent_count = 0
    read_blocks = skipped_blocks = 0
    timings = dict(load_inputs=0.0, mask_read=0.0, source_read=0.0,
                   aggregation_and_statistics=0.0, write=0.0)
    with rasterio.open(SOURCE) as src, rasterio.open(mask_path) as mask:
        transform, col, row = validate_alignment(src, mask, grid)
        if src.scales != (1.0,) or src.offsets != (0.0,):
            raise ValueError('Source has scale/offset metadata; verify encoding before processing')
        source_metadata = dict(crs=src.crs.to_string(), resolution_m=list(src.res),
                               dtype=src.dtypes[0], width=src.width, height=src.height,
                               transform=list(src.transform)[:6], scale=src.scales[0],
                               offset=src.offsets[0],
                               mask_flags=[flag.name for flag in src.mask_flag_enums[0]])
        timings['load_inputs'] = perf_counter() - started
        folder.mkdir(parents=True, exist_ok=True)
        total = ((width+block_size-1)//block_size)*((height+block_size-1)//block_size)
        print(f'{code}: decoding 25 m slope and averaging to {width} x {height} at 100 m', flush=True)
        with tempfile.TemporaryDirectory(prefix=f'{code}_slope_', dir=folder) as tmp:
            temporary = Path(tmp)
            profile = reference.profile(width, height, transform, 'float32', NODATA)
            with rasterio.open(temporary/names[0], 'w', **profile) as deg_dst, \
                    rasterio.open(temporary/names[1], 'w', **profile) as res_dst:
                for number, win in enumerate(reference.windows(width, height, block_size), 1):
                    stage_started = perf_counter()
                    mask_values = mask.read(1, window=win)
                    if np.any((mask_values != 0) & (mask_values != 1)):
                        raise ValueError('Population-valid mask must contain only zero and one')
                    included = mask_values == 1
                    included_count += int(included.sum())
                    timings['mask_read'] += perf_counter() - stage_started
                    if included.any():
                        stage_started = perf_counter()
                        source_win = Window(col+win.col_off*FACTOR, row+win.row_off*FACTOR,
                                            win.width*FACTOR, win.height*FACTOR)
                        raw = src.read(1, window=source_win, masked=True, boundless=True)
                        read_blocks += 1
                        timings['source_read'] += perf_counter() - stage_started
                        stage_started = perf_counter()
                        degrees, resistance, keep, count = aggregate_slope(raw, included, missing)
                        complete_count += int(np.count_nonzero(included & (count == FACTOR**2)))
                        partial_count += int(np.count_nonzero(included & (count > 0) & (count < FACTOR**2)))
                        absent_count += int(np.count_nonzero(included & (count == 0)))
                    else:
                        stage_started = perf_counter()
                        skipped_blocks += 1
                        degrees = np.full(included.shape, NODATA, dtype='float32')
                        resistance = degrees.copy()
                        keep = included
                    valid_count += int(keep.sum())
                    if keep.any():
                        saved_degrees = degrees[keep]
                        saved_resistance = resistance[keep]
                        low = min(low, float(saved_degrees.min()))
                        high = max(high, float(saved_degrees.max()))
                        resistance_low = min(resistance_low, float(saved_resistance.min()))
                        resistance_high = max(resistance_high, float(saved_resistance.max()))
                        degrees_sum += float(saved_degrees.sum(dtype='float64'))
                        resistance_sum += float(saved_resistance.sum(dtype='float64'))
                    timings['aggregation_and_statistics'] += perf_counter() - stage_started
                    stage_started = perf_counter()
                    deg_dst.write(degrees, 1, window=win)
                    res_dst.write(resistance, 1, window=win)
                    timings['write'] += perf_counter() - stage_started
                    if number == 1 or number % 100 == 0 or number == total:
                        print(f'  blocks {number}/{total}', flush=True)
                if included_count != grid['valid_population_cells'] or valid_count == 0:
                    raise ValueError('Reference mask count mismatch or no usable slope cells')
                for dst in (deg_dst, res_dst):
                    dst.update_tags(source_encoding='degrees(acos(DN/250))',
                                    conversion_source=CONVERSION_SOURCE, method_version=METHOD_VERSION,
                                    aggregation='mean of decoded degrees, 4 x 4 cells', missing_policy=missing)
                deg_dst.update_tags(units='degrees')
                res_dst.update_tags(units='dimensionless', resistance_formula='1 + 19 * degrees / 90')
            timings['total_raster_stage'] = perf_counter() - started
            missing_count = included_count - valid_count
            missing_percentage = 100.0 * missing_count / included_count
            coverage = dict(
                expected_source_cells_per_output_cell=FACTOR**2,
                complete_cells=complete_count, partial_cells=partial_count, absent_cells=absent_count,
                percentages=dict(complete=100.0 * complete_count / included_count,
                                 partial=100.0 * partial_count / included_count,
                                 absent=100.0 * absent_count / included_count),
                denominator='Population-valid 100 m cells, before applying the slope missing-data policy')
            report = dict(report_schema_version=1, method_version=METHOD_VERSION,
                          method='decoded_degree_mean_linear_1_20',
                          country=code, outputs=names, grid=grid, source=str(SOURCE),
                          source_signature=reference.fingerprint(SOURCE), source_nodata=repr(src.nodata),
                          source_metadata=source_metadata, conversion_source=CONVERSION_SOURCE,
                          publication=PUBLICATION,
                          mask_signature=reference.fingerprint(mask_path),
                          encoding='degrees(acos(DN/250))',
                          resistance_formula='1 + 19 * mean_degrees / 90',
                          aggregation='MEAN after decoding', factor=FACTOR, missing_policy=missing,
                          valid_cells=valid_count, population_valid_cells=included_count,
                          nodata_cells=width*height-valid_count,
                          missing_slope_cells_within_population_mask=missing_count,
                          missing_slope_percentage_within_population_mask=missing_percentage,
                          source_coverage=coverage,
                          minimum_degrees=low, maximum_degrees=high,
                          mean_degrees=degrees_sum/valid_count,
                          multiplier_statistics=dict(min=resistance_low, max=resistance_high,
                                                     mean=resistance_sum/valid_count),
                          statistics_scope='Valid Float32 output cells only; NoData excluded; '
                                           'means are arithmetic means of the values written to the TIFFs',
                          background=1, nodata=NODATA, block_size=block_size,
                          processing_blocks=dict(total=total, source_read=read_blocks,
                                                 skipped_empty=skipped_blocks),
                          timings_seconds=timings,
                          timing_scope='Per-country processing through closing both temporary TIFFs; '
                                       'excludes the main command reference preflight, JSON writing and '
                                       'final replacements. Write time measures '
                                       'block write calls; total includes raster closing, setup and overhead.')
            (temporary/report_name).write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
            for name in names+[report_name]:
                (temporary/name).replace(folder/name)
    print(f'{code}: saved slope outputs; {valid_count:,} valid cells; '
          f'{missing_count:,} population-valid cells without output slope ({missing_percentage:.2f}%); '
          f'mean multiplier {resistance_sum/valid_count:.4f}; '
          f'{timings["total_raster_stage"]:.2f} s', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', nargs='+', choices=COUNTRIES, default=COUNTRIES)
    parser.add_argument('--missing', choices=('propagate', 'ignore'), default=MISSING_POLICY)
    args = parser.parse_args()
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    grids = {code: load_grid(code) for code in args.countries}
    for code, grid in grids.items():
        process_country(code, grid, args.missing)


if __name__ == '__main__':
    main()
