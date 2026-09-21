#!/usr/bin/env python3
"""Classify population counts on the existing 100 m master grid.

Resistance reference:
    Bogs, S., Abdelshafy, A., and Walther, G.
    Planning minimum regret CO2 pipeline networks. IISE Transactions.
    https://doi.org/10.1080/24725854.2025.2602823

The population thresholds in Bogs et al. are people per square kilometre.
Divide them by 100 to express the same thresholds as people per hectare.
The JRC-ESTAT Census Population Grid 2021 contains resident counts per 100 m
cell. One 100 m x 100 m cell is one hectare: the source population values
need no numerical conversion, resampling, rounding or interpolation.

Implementation convention for exact thresholds (explicit to avoid gaps):
    [0, 2.5) -> 1; [2.5, 5) -> 4; [5, 20) -> 9;
    [20, 40) -> 16; [40, 80) -> 25; [80, infinity) -> 36.
This endpoint convention also matches the authors' released classifier.

Inputs: population TIFF, country reference JSONs and population-valid masks.
Outputs: intermediate/population/COUNTRY_population_resistance_100m.tif
         and COUNTRY_population_resistance.json.
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
BLOCK_SIZE = 256  # Processing chunk width/height in 100 m cells; no resampling.
NODATA = -9999.0
THRESHOLDS_PEOPLE_PER_KM2 = np.array([250, 500, 2000, 4000, 8000], dtype='float64')
HECTARES_PER_KM2 = 100.0
THRESHOLDS = THRESHOLDS_PEOPLE_PER_KM2 / HECTARES_PER_KM2
MULTIPLIERS = np.array([1, 4, 9, 16, 25, 36], dtype='float32')
PUBLICATION = {
    'authors': 'Bogs, S.; Abdelshafy, A.; Walther, G.',
    'title': 'Planning minimum regret CO2 pipeline networks',
    'journal': 'IISE Transactions',
    'doi': 'https://doi.org/10.1080/24725854.2025.2602823',
    'threshold_source': 'Bogs et al., arXiv v1, Appendix B, Table 5 (people per square kilometre)',
    'threshold_table_url': 'https://arxiv.org/html/2502.12035v1#A2.T5',
    'released_code_url': 'https://zenodo.org/records/15829448',
    'population_dataset_url': 'https://data.jrc.ec.europa.eu/dataset/98336641-fd1c-4992-8c7b-c470dd5eb81e',
}
INTERVAL_RULE = 'Lower-inclusive, upper-exclusive intervals; exact 80 belongs to class 36'


def classify_population(values, included):
    """Classify included cells; preserve fractional counts and valid zero counts."""
    values = np.asarray(values)
    included = np.asarray(included, dtype=bool)
    if values.shape != included.shape:
        raise ValueError('Population and mask shapes differ')
    selected = values[included]
    if np.any(~np.isfinite(selected)) or np.any(selected < 0):
        raise ValueError('Included population values must be finite and nonnegative')
    out = np.full(values.shape, NODATA, dtype='float32')
    out[included] = MULTIPLIERS[np.searchsorted(THRESHOLDS, selected, side='right')]
    return out


def load_grid(code):
    path = reference.REFERENCE_DIR / f'{code}_reference_grid.json'
    grid = json.loads(path.read_text())
    if grid.get('schema_version') != 1 or grid.get('country') != code:
        raise ValueError(f'Unsupported or mismatched grid definition: {path}')
    if rasterio.crs.CRS.from_string(grid['crs']) != rasterio.crs.CRS.from_string(reference.CRS):
        raise ValueError(f'Grid CRS must be {reference.CRS}: {path}')
    for label, source in [('population', reference.POPULATION_PATH),
                          ('boundary', reference.boundary_path(code))]:
        if grid[f'{label}_signature'] != reference.fingerprint(source):
            raise ValueError(f'{code}: {label} source changed; rerun prepare_reference_grid.py')
    return grid


def validate_alignment(src, mask, grid):
    width, height = grid['width'], grid['height']
    if (not isinstance(width, int) or not isinstance(height, int)
            or width <= 0 or height <= 0):
        raise ValueError('Reference dimensions must be positive integers')
    offsets = grid['population_window']
    if len(offsets) != 4 or any(not isinstance(v, int) for v in offsets):
        raise ValueError('Population window must contain four integer values')
    col, row, w, h = offsets
    if (w != width or h != height or col < 0 or row < 0
            or col+w > src.width or row+h > src.height):
        raise ValueError('Reference population window is inconsistent or outside source')
    transform = Affine(*grid['transform'])
    if not np.allclose([transform.a, transform.b, transform.d, transform.e],
                       [100, 0, 0, -100], atol=1e-8, rtol=0):
        raise ValueError('Expected a north-up 100 m reference grid')
    expected_crs = rasterio.crs.CRS.from_string(grid['crs'])
    if src.crs != expected_crs or mask.crs != expected_crs:
        raise ValueError('Population, mask and reference CRS differ')
    if src.count != 1 or mask.count != 1:
        raise ValueError('Expected single-band population and mask rasters')
    if (mask.width, mask.height) != (width, height) or mask.nodata != 0:
        raise ValueError('Population-valid mask dimensions or NoData definition differ from reference')
    for actual in (src.window_transform(Window(*offsets)), mask.transform):
        if not np.allclose(list(actual)[:6], list(transform)[:6], atol=1e-7, rtol=0):
            raise ValueError('Population or mask alignment differs from reference; no resampling is allowed')
    return transform


def process_country(code, grid=None, block_size=BLOCK_SIZE):
    started = perf_counter()
    if block_size <= 0:
        raise ValueError('Block size must be positive')
    if grid is None:
        grid = load_grid(code)
    folder = reference.PIPELINE_DIR / 'intermediate' / 'population'
    mask_path = reference.REFERENCE_DIR / grid['population_valid_mask']
    target_name = f'{code}_population_resistance_100m.tif'
    report_name = f'{code}_population_resistance.json'
    counts = {str(int(v)): 0 for v in MULTIPLIERS}
    valid_count = 0
    minimum, maximum, population_sum = float('inf'), float('-inf'), 0.0
    timings = dict(load_inputs=0.0, mask_read=0.0, population_read=0.0,
                   classification_and_statistics=0.0, write=0.0)
    with rasterio.open(reference.POPULATION_PATH) as src, rasterio.open(mask_path) as mask:
        transform = validate_alignment(src, mask, grid)
        width, height = grid['width'], grid['height']
        col, row, _, _ = grid['population_window']
        total = ((width+block_size-1)//block_size)*((height+block_size-1)//block_size)
        blocks = dict(total=total, population_read=0, skipped_empty=0)
        folder.mkdir(parents=True, exist_ok=True)
        timings['load_inputs'] = perf_counter() - started
        print(f'{code}: classifying {width} x {height} cells at 100 m', flush=True)
        with tempfile.TemporaryDirectory(prefix=f'{code}_population_', dir=folder) as tmp:
            temporary = Path(tmp)
            with rasterio.open(temporary/target_name, 'w', **reference.profile(
                    width, height, transform, 'float32', NODATA)) as dst:
                for number, win in enumerate(reference.windows(width, height, block_size), start=1):
                    tick = perf_counter()
                    raw_mask = mask.read(1, window=win)
                    if np.any((raw_mask != 0) & (raw_mask != 1)):
                        raise ValueError('Population-valid mask must contain only 0 and 1')
                    included = raw_mask == 1
                    timings['mask_read'] += perf_counter() - tick
                    if included.any():
                        tick = perf_counter()
                        source_win = Window(col+win.col_off, row+win.row_off, win.width, win.height)
                        pop = src.read(1, window=source_win, masked=True)
                        blocks['population_read'] += 1
                        timings['population_read'] += perf_counter() - tick
                        tick = perf_counter()
                        if np.any(included & np.ma.getmaskarray(pop)):
                            raise ValueError('Mask includes source NoData; rerun reference-grid preparation')
                        classified = classify_population(pop.data, included)
                        selected = pop.data[included]
                        minimum = min(minimum, float(selected.min()))
                        maximum = max(maximum, float(selected.max()))
                        population_sum += float(selected.sum(dtype=np.float64))
                        valid_count += int(selected.size)
                        vals, frequencies = np.unique(classified[included], return_counts=True)
                        for value, count in zip(vals, frequencies):
                            counts[str(int(value))] += int(count)
                        timings['classification_and_statistics'] += perf_counter() - tick
                    else:
                        # All output pixels are known to be NoData; no source read is needed.
                        blocks['skipped_empty'] += 1
                        classified = np.full(included.shape, NODATA, dtype='float32')
                    tick = perf_counter()
                    dst.write(classified, 1, window=win)
                    timings['write'] += perf_counter() - tick
                    if number == 1 or number % 100 == 0 or number == total:
                        print(f'  blocks {number}/{total}', flush=True)
                if valid_count != grid['valid_population_cells'] or not valid_count:
                    raise ValueError('Mask valid-cell count disagrees with reference JSON; rerun grid preparation')
                if sum(counts.values()) != valid_count:
                    raise ValueError('Population class counts disagree with valid-cell count')
                dst.update_tags(population_units='people per 100 m cell = people per hectare',
                                units='dimensionless', background_multiplier=1,
                                layer_combination='multiply completed 100 m layer factors downstream',
                                classification_thresholds=json.dumps(THRESHOLDS.tolist()),
                                original_thresholds_people_per_km2=json.dumps(THRESHOLDS_PEOPLE_PER_KM2.tolist()),
                                classification_multipliers=json.dumps(MULTIPLIERS.tolist()),
                                interval_rule=INTERVAL_RULE, publication_doi=PUBLICATION['doi'])
            timings['total_raster_stage'] = perf_counter() - started
            used_multipliers = [int(value) for value, count in counts.items() if count]
            mean_multiplier = sum(int(value) * count for value, count in counts.items()) / valid_count
            report = dict(country=code, output=target_name, crs=grid['crs'],
                          width=width, height=height, transform=grid['transform'],
                          grid=grid, mask_signature=reference.fingerprint(mask_path),
                          publication=PUBLICATION, interval_rule=INTERVAL_RULE,
                          thresholds_people_per_km2=THRESHOLDS_PEOPLE_PER_KM2.tolist(),
                          thresholds_people_per_hectare=THRESHOLDS.tolist(),
                          population_units='people per 100 m cell = people per hectare',
                          threshold_conversion='people per square kilometre / 100 = people per hectare',
                          multipliers=MULTIPLIERS.tolist(), valid_cells=valid_count,
                          nodata_cells=width*height-valid_count, class_cell_counts=counts,
                          class_cell_percentages={value: 100.0 * count / valid_count
                                                  for value, count in counts.items()},
                          class_percentage_denominator='valid output cells only; NoData excluded',
                          statistics=dict(min=min(used_multipliers), max=max(used_multipliers),
                                          mean=mean_multiplier,
                                          mean_definition='arithmetic mean of valid saved multiplier cells'),
                          included_population_min=minimum, included_population_max=maximum,
                          included_population_sum=population_sum, block_size=block_size,
                          background=1, nodata=NODATA, resampling='none',
                          processing_blocks=blocks, timings_seconds=timings,
                          timing_scope='per-country processing through closing the temporary TIFF; excludes JSON writing and final file replacements')
            (temporary/report_name).write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
            (temporary/target_name).replace(folder/target_name)
            (temporary/report_name).replace(folder/report_name)
    print(f'Saved {folder/target_name}; {valid_count:,} classified cells', flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', nargs='+', default=COUNTRIES)
    args = parser.parse_args()
    grids = {code: load_grid(code) for code in args.countries}
    for code in args.countries:
        process_country(code, grids[code])


if __name__ == '__main__':
    main()
