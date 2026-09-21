#!/usr/bin/env python3
"""Build coverage-weighted network multipliers on the common 100 m grid.

10 m all-touched line occupancy is averaged as 1 + f * (network_factor - 1).
Fine pixels are weighted by their area inside the supplied country boundary.
No physical network buffer is implied. Each network is saved separately;
the completed 100 m layer factors are multiplied in the integration stage.

Keep the existing prepare_reference_grid.py beside this script and run it first.
Requires Python 3.10+, Shapely 2+, GeoPandas, Rasterio, NumPy and PyProj.
Factors: https://doi.org/10.1080/24725854.2025.2602823
The fractional-coverage method is a CO2RouteX adaptation, not an exact
reproduction of the authors' final-cell intersection rule.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from time import perf_counter

import geopandas as gpd
import numpy as np
import rasterio
from affine import Affine
from rasterio.features import rasterize
from rasterio.windows import Window, transform as window_transform, bounds as window_bounds
import shapely

import prepare_reference_grid as reference

COUNTRIES = ('NL', 'DE', 'NO')
FEATURE_VALUES = {'motorways': 3.0, 'pipelines': 0.25, 'railways': 3.0}
LAYER_NAME = 'lines'
SAVE_10M_RASTERS = False
BLOCK_SIZE = 128  # Coarse cells per edge; fine arrays have a one-pixel halo.
GEOMETRY_BATCH = 4096
FACTOR = 10
BACKGROUND = 1.0
NODATA = -9999.0
METHOD_VERSION = 2  # Invalidate the former zero-background contribution rasters.
CRS = 'EPSG:3035'


def file_signature(path):
    """Local-cache identity: resolved path, byte size and modification time."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    paths = [path]
    if path.suffix.lower() == '.shp':
        paths = [path.with_suffix(s) for s in ('.shp', '.shx', '.dbf', '.prj')]
        paths += [path.with_suffix(s) for s in ('.cpg', '.qix', '.sbn', '.sbx')
                  if path.with_suffix(s).is_file()]
    return [{'path': str(p), 'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
            for p in paths]


def _same_transform(first, second):
    return np.allclose(list(first)[:6], list(second)[:6], rtol=0, atol=1e-8)


def load_grid(code):
    if code not in COUNTRIES:
        raise ValueError(f'Unsupported country: {code}')
    path = reference.REFERENCE_DIR / f'{code}_reference_grid.json'
    grid = json.loads(path.read_text(encoding='utf-8'))
    if (grid.get('schema_version') != 1 or grid.get('country') != code
            or grid.get('factor') != FACTOR):
        raise ValueError(f'Unsupported reference grid: {path}')
    width, height = grid['width'], grid['height']
    if any(type(n) is not int or n <= 0 for n in (width, height)):
        raise ValueError('Reference grid dimensions must be positive integers')
    transform, fine = Affine(*grid['transform']), Affine(*grid['fine_transform'])
    if (rasterio.crs.CRS.from_string(grid['crs']) != rasterio.crs.CRS.from_string(CRS)
            or not np.isfinite(list(transform)[:6]).all()
            or not np.allclose([transform.a, transform.b, transform.d, transform.e],
                               [100, 0, 0, -100], rtol=0, atol=1e-8)
            or not _same_transform(fine, transform * Affine.scale(1 / FACTOR))
            or grid['fine_width'] != width * FACTOR
            or grid['fine_height'] != height * FACTOR):
        raise ValueError('Expected aligned 100 m / 10 m grids in EPSG:3035')
    mask_name = grid['population_valid_mask']
    if not isinstance(mask_name, str) or Path(mask_name).name != mask_name:
        raise ValueError('Reference mask must be a filename within REFERENCE_DIR')
    if (type(grid['valid_population_cells']) is not int
            or not 0 < grid['valid_population_cells'] <= width * height):
        raise ValueError('Invalid reference valid-cell count')
    for label, source in [('population', reference.POPULATION_PATH),
                          ('boundary', reference.boundary_path(code))]:
        if grid[f'{label}_signature'] != reference.fingerprint(source):
            raise ValueError(f'{code}: {label} changed; rerun prepare_reference_grid.py')
    return grid


def validate_mask(mask, grid):
    if (mask.crs != rasterio.crs.CRS.from_string(CRS)
            or (mask.width, mask.height) != (grid['width'], grid['height'])
            or mask.count != 1 or mask.nodata != 0
            or not _same_transform(mask.transform, Affine(*grid['transform']))):
        raise ValueError('Population-valid mask does not match the reference grid')


def read_network(path):
    frame = gpd.read_file(path, layer=LAYER_NAME)
    if frame.crs is None:
        raise ValueError(f'Network CRS missing: {path}')
    for stage in ('source', 'projected'):
        if stage == 'projected':
            frame = frame.to_crs(CRS)
        if (frame.geometry.isna().any() or frame.geometry.is_empty.any()
                or not frame.geometry.is_valid.all()
                or not frame.geom_type.isin(['LineString', 'MultiLineString']).all()
                or (not frame.empty and not np.isfinite(frame.total_bounds).all())):
            raise ValueError(f'Expected valid nonempty line geometries ({stage}): {path}')
    return frame  # A valid empty layer is allowed and produces neutral values.


def _line_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'LineString':
        return [geometry] if geometry.length > 0 else []
    if geometry.geom_type in ('MultiLineString', 'GeometryCollection'):
        return [part for child in geometry.geoms for part in _line_parts(child)]
    return []  # A point-only contact with the country has no retained line length.


def _polygon_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry]
    if geometry.geom_type in ('MultiPolygon', 'GeometryCollection'):
        return [part for child in geometry.geoms for part in _polygon_parts(child)]
    return []


def country_weights(domain, transform, shape, included=None):
    """Fraction of each fine pixel inside the country, exact at polygon edges.

This measures the country portion of raster pixel footprints, not the area
of zero-width network lines. included is an optional fine-resolution mask.
"""
    height, width = shape
    if (transform.a <= 0 or transform.e >= 0 or transform.b or transform.d):
        raise ValueError('Expected a north-up grid for country area weighting')
    if included is None:
        included = np.ones(shape, dtype=bool)
    included = np.asarray(included, dtype=bool)
    if included.shape != tuple(shape):
        raise ValueError('Fine inclusion mask has an unexpected shape')
    if domain.is_empty or domain.area <= 0 or not included.any():
        return np.zeros(shape, dtype='float64')
    footprint = shapely.box(*window_bounds(Window(0, 0, width, height), transform))
    if shapely.covers(domain, footprint):
        return included.astype('float64')
    weights = rasterize([(domain, 1)], out_shape=shape, transform=transform,
                        fill=0, dtype='uint8', all_touched=False).astype('float64')
    edges = rasterize([(domain.boundary, 1)], out_shape=shape, transform=transform,
                      fill=0, dtype='uint8', all_touched=True)
    rows, cols = np.nonzero((edges != 0) & included)
    pixel_area = abs(transform.a * transform.e)
    for start in range(0, len(rows), GEOMETRY_BATCH):
        rr, cc = rows[start:start + GEOMETRY_BATCH], cols[start:start + GEOMETRY_BATCH]
        x, y = transform.c + cc * transform.a, transform.f + rr * transform.e
        pixels = shapely.box(x, y + transform.e, x + transform.a, y)
        weights[rr, cc] = shapely.area(shapely.intersection(pixels, domain)) / pixel_area
    if (not np.isfinite(weights).all() or np.any(weights < -1e-8)
            or np.any(weights > 1 + 1e-8)):
        raise ValueError('Invalid fine-pixel country intersection area')
    weights = np.clip(weights, 0, 1)
    weights[~included] = 0
    return weights


def fine_block(lines, boundary, fine_transform, shape, included=None):
    """Binary line occupancy plus country-area weights, with a one-pixel halo."""
    height, width = shape
    halo_transform = fine_transform * Affine.translation(-1, -1)
    halo_shape = (height + 2, width + 2)
    footprint = shapely.box(*window_bounds(Window(0, 0, width + 2, height + 2), halo_transform))
    entirely_inside = bool(shapely.covers(boundary, footprint))
    domain = (footprint if entirely_inside else
              shapely.union_all(_polygon_parts(shapely.intersection(boundary, footprint))))
    weights = country_weights(domain, fine_transform, shape, included=included)
    present = np.zeros(shape, dtype='uint8')
    if not np.any(weights > 0):
        return present, weights
    ids = lines.sindex.query(footprint, predicate='intersects')
    geometries = lines.geometry.iloc[ids].to_numpy()
    if not entirely_inside and len(geometries):
        # Only line portions inside the country can influence country pixels.
        geometries = [part for g in shapely.intersection(geometries, domain)
                      for part in _line_parts(g)]
    if len(geometries):
        present = rasterize(((g, 1) for g in geometries), out_shape=halo_shape,
                            transform=halo_transform, fill=0, dtype='uint8',
                            all_touched=True)[1:-1, 1:-1]
    return present, weights


def aggregate(present, weights, value, factor=FACTOR, included=None):
    """Area-weighted arithmetic mean of fine factors (value if present else 1)."""
    present, weights = np.asarray(present), np.asarray(weights, dtype='float64')
    if type(factor) is not int or factor <= 0 or present.ndim != 2:
        raise ValueError('Expected a positive integer factor and 2-D arrays')
    height, width = present.shape
    if height % factor or width % factor or weights.shape != present.shape:
        raise ValueError('Fine arrays must have equal shapes and complete coarse groups')
    if (not np.isfinite(value) or value <= 0 or not np.isfinite(weights).all()
            or np.any((weights < 0) | (weights > 1))
            or np.any((present != 0) & (present != 1))):
        raise ValueError('Expected positive factor, binary occupancy and weights in [0, 1]')
    grouped_shape = (height // factor, factor, width // factor, factor)
    denominator = weights.reshape(grouped_shape).sum(axis=(1, 3))
    numerator = (present * weights).reshape(grouped_shape).sum(axis=(1, 3))
    if included is None:
        included = np.ones(denominator.shape, dtype=bool)
    included = np.asarray(included, dtype=bool)
    if included.shape != denominator.shape:
        raise ValueError('Coarse inclusion mask has an unexpected shape')
    if np.any(included & (denominator <= 0)):
        raise ValueError('Master mask includes cells with zero country area; check boundary/reference grid')
    result = np.full(denominator.shape, NODATA, dtype='float32')
    fractions = numerator[included] / denominator[included]
    if np.any(fractions < -1e-8) or np.any(fractions > 1 + 1e-8):
        raise ValueError('Occupied country fraction is outside [0, 1]')
    values = BACKGROUND + (value - BACKGROUND) * np.clip(fractions, 0, 1)
    result[included] = np.clip(values, min(BACKGROUND, value), max(BACKGROUND, value))
    return result


def _read_report(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _inputs(paths):
    return {name: file_signature(path) for name, path in paths.items()}


def _current_report(report, key, output, fine_output, save_10m):
    if not isinstance(report, dict) or report.get('cache_key') != key:
        return False
    try:
        if report.get('output_signature') != file_signature(output):
            return False
        if save_10m and report.get('fine_output_signature') != file_signature(fine_output):
            return False
    except OSError:
        return False
    return True


def process(code, feature, grid, block_size=BLOCK_SIZE, save_10m=SAVE_10M_RASTERS,
            overwrite=False):
    if feature not in FEATURE_VALUES or type(block_size) is not int or block_size <= 0:
        raise ValueError('Expected a supported feature and positive integer block size')
    started = perf_counter()
    folder = reference.PIPELINE_DIR / 'intermediate' / f'existing_{feature}'
    source = folder / f'{code}_{feature}.gpkg'
    output = folder / f'{code}_{feature}_resistance_100m.tif'
    fine_output = folder / f'{code}_{feature}_resistance_10m.tif'
    report_path = folder / f'{code}_{feature}_resistance.json'
    paths = dict(network=source, grid=reference.REFERENCE_DIR / f'{code}_reference_grid.json',
                 mask=reference.REFERENCE_DIR / grid['population_valid_mask'],
                 boundary=Path(reference.boundary_path(code)), population=reference.POPULATION_PATH)
    before = _inputs(paths)
    if load_grid(code) != grid:
        raise ValueError('Reference JSON changed after loading; retry with the current grid')
    value = FEATURE_VALUES[feature]
    key = dict(method_version=METHOD_VERSION, inputs=before, feature=feature,
               layer=LAYER_NAME, feature_multiplier=value, background=BACKGROUND,
               nodata=NODATA, factor=FACTOR, all_touched=True, buffer_m=0,
               aggregation='arithmetic-area-weighted-fine-pixel-factors',
               denominator='fine-pixel-country-intersection-area',
               line_clip='country boundary before occupancy',
               mask='reference population-valid 100 m mask', save_10m=bool(save_10m))
    with rasterio.open(paths['mask']) as mask:
        validate_mask(mask, grid)
        old = _read_report(report_path)
        if not overwrite and _current_report(old, key, output, fine_output, save_10m):
            if _inputs(paths) != before:
                raise RuntimeError('Inputs changed during cache check; retry')
            print(f'{code}/{feature}: reusing current raster: {output}', flush=True)
            return old
        lines = read_network(source)
        boundary_frame = reference.read_boundary(paths['boundary']).to_crs(CRS)
        boundary = shapely.union_all(boundary_frame.geometry.to_numpy())
        if boundary.is_empty or not boundary.is_valid or boundary.area <= 0:
            raise ValueError('Country boundary has no valid polygon area')
        shapely.prepare(boundary)
        width, height = grid['width'], grid['height']
        transform, fine_transform = Affine(*grid['transform']), Affine(*grid['fine_transform'])
        total = ((width + block_size - 1) // block_size) * ((height + block_size - 1) // block_size)
        counts = dict(valid_cells=0, mask_valid_cells=0, neutral_cells=0,
                      influenced_cells=0, partial_domain_cells=0)
        minimum, maximum, value_sum = float('inf'), float('-inf'), 0.0
        domain_m2 = occupied_m2 = 0.0
        timings = dict(load_inputs=perf_counter() - started, mask_read=0.0,
                       coverage_and_aggregation=0.0, write=0.0)
        tags = dict(method_version=METHOD_VERSION, background_multiplier=BACKGROUND,
                    feature_multiplier=value, formula='1 + occupied_country_fraction * (feature_multiplier - 1)',
                    coverage='10 m GDAL all_touched line occupancy; no physical buffer',
                    aggregation='arithmetic mean weighted by exact fine-pixel country area',
                    master_mask='population-valid 100 m mask', units='dimensionless',
                    layer_combination='multiply completed 100 m layer factors downstream',
                    minimum_multiplier=min(1, value), maximum_multiplier=max(1, value))
        print(f'{code}/{feature}: {len(lines):,} source features; {width} x {height}; {total:,} blocks', flush=True)
        with tempfile.TemporaryDirectory(prefix=f'{code}_{feature}_raster_', dir=folder) as tmp:
            temp = Path(tmp)
            with ExitStack() as stack:
                dst = stack.enter_context(rasterio.open(temp / output.name, 'w', **reference.profile(
                    width, height, transform, 'float32', NODATA)))
                fine_dst = None
                if save_10m:
                    fine_dst = stack.enter_context(rasterio.open(temp / fine_output.name, 'w', **reference.profile(
                        width * FACTOR, height * FACTOR, fine_transform, 'float32', NODATA)))
                for number, win in enumerate(reference.windows(width, height, block_size), 1):
                    tick = perf_counter()
                    raw = mask.read(1, window=win)
                    if np.any((raw != 0) & (raw != 1)):
                        raise ValueError('Master mask must contain only 0 and 1')
                    included = raw == 1
                    counts['mask_valid_cells'] += int(included.sum())
                    timings['mask_read'] += perf_counter() - tick
                    fine_win = Window(win.col_off * FACTOR, win.row_off * FACTOR,
                                      win.width * FACTOR, win.height * FACTOR)
                    fine_shape = (int(fine_win.height), int(fine_win.width))
                    out = np.full(included.shape, NODATA, dtype='float32')
                    fine_values = np.full(fine_shape, NODATA, dtype='float32') if save_10m else None
                    if included.any():
                        tick = perf_counter()
                        fine_included = np.repeat(np.repeat(included, FACTOR, axis=0), FACTOR, axis=1)
                        present, weights = fine_block(lines, boundary,
                            window_transform(fine_win, fine_transform), fine_shape, included=fine_included)
                        out = aggregate(present, weights, value, included=included)
                        samples = out[included]
                        if (not np.isfinite(samples).all() or np.any(samples < min(1, value))
                                or np.any(samples > max(1, value))):
                            raise ValueError('Network multiplier outside its permitted range')
                        counts['valid_cells'] += len(samples)
                        neutral = int(np.count_nonzero(samples == BACKGROUND))
                        counts['neutral_cells'] += neutral
                        counts['influenced_cells'] += len(samples) - neutral
                        area = weights.reshape(included.shape[0], FACTOR, included.shape[1], FACTOR).sum(axis=(1, 3))
                        counts['partial_domain_cells'] += int(np.count_nonzero(included & (area < FACTOR**2 - 1e-8)))
                        fine_area = abs(fine_transform.a * fine_transform.e)
                        domain_m2 += float(weights.sum()) * fine_area
                        occupied_m2 += float((present * weights).sum()) * fine_area
                        minimum, maximum = min(minimum, float(samples.min())), max(maximum, float(samples.max()))
                        value_sum += float(samples.sum(dtype='float64'))
                        if fine_values is not None:
                            fine_values = np.where(weights > 0, np.where(present != 0, value, BACKGROUND), NODATA).astype('float32')
                        timings['coverage_and_aggregation'] += perf_counter() - tick
                    tick = perf_counter()
                    dst.write(out, 1, window=win)
                    if fine_dst is not None:
                        fine_dst.write(fine_values, 1, window=fine_win)
                    timings['write'] += perf_counter() - tick
                    if number == 1 or number % 100 == 0 or number == total:
                        print(f'  blocks {number:,}/{total:,}; {perf_counter() - started:.1f} s', flush=True)
                if not (counts['valid_cells'] == counts['mask_valid_cells'] == grid['valid_population_cells']):
                    raise ValueError('Output valid-cell count differs from the reference mask/grid')
                dst.update_tags(**tags, resolution_m=100)
                if fine_dst is not None:
                    fine_tags = dict(tags, resolution_m=10,
                        aggregation='none; per-pixel factor; country-area weights required to reproduce 100 m boundary means')
                    fine_dst.update_tags(**fine_tags)
            if _inputs(paths) != before:
                raise RuntimeError('Inputs changed during processing; completed outputs not replaced. Retry.')
            timings['total'] = perf_counter() - started
            report = dict(country=code, feature=feature, method_version=METHOD_VERSION,
                          cache_key=key, grid=grid, input_file=str(source), input_signature=before['network'],
                          source_features=len(lines), output=output.name,
                          fine_output=fine_output.name if save_10m else None,
                          block_size=block_size, background=BACKGROUND, nodata=NODATA,
                          feature_multiplier=value, fine_grid_used=True, **counts,
                          statistics={'min': minimum, 'max': maximum, 'mean': value_sum / counts['valid_cells'],
                                      'mean_definition': 'arithmetic mean of valid saved 100 m values'},
                          area_totals_m2={'country_in_valid_cells': domain_m2,
                                          'occupied_pixel_country_area': occupied_m2},
                          timings_seconds=timings, metadata=tags)
            # Publish report last. Interrupted multi-file publication invalidates reuse
            # through the stored output signatures; the next run rebuilds that layer.
            (temp / output.name).replace(output)
            if save_10m:
                (temp / fine_output.name).replace(fine_output)
            report['output_signature'] = file_signature(output)
            report['fine_output_signature'] = file_signature(fine_output) if save_10m else None
            staged_report = temp / report_path.name
            staged_report.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
            staged_report.replace(report_path)
    print(f'Saved {output}; {counts["valid_cells"]:,} valid cells; range {minimum:g}..{maximum:g}', flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', nargs='+', choices=COUNTRIES, default=COUNTRIES)
    parser.add_argument('--features', nargs='+', choices=FEATURE_VALUES, default=list(FEATURE_VALUES))
    parser.add_argument('--save-10m', action='store_true', default=SAVE_10M_RASTERS)
    parser.add_argument('--overwrite', action='store_true', help='Rebuild even if cached outputs are current')
    parser.add_argument('--block-size', type=int, default=BLOCK_SIZE, help='100 m cells per block edge')
    args = parser.parse_args()
    if args.block_size <= 0:
        parser.error('--block-size must be positive')
    if int(shapely.__version__.split('.')[0]) < 2:
        raise RuntimeError('Shapely 2 or newer is required')
    countries, features = list(dict.fromkeys(args.countries)), list(dict.fromkeys(args.features))
    grids = {code: load_grid(code) for code in countries}
    # Check all selected files and masks before overwriting any layer.
    for code in countries:
        with rasterio.open(reference.REFERENCE_DIR / grids[code]['population_valid_mask']) as mask:
            validate_mask(mask, grids[code])
        for feature in features:
            path = reference.PIPELINE_DIR / 'intermediate' / f'existing_{feature}' / f'{code}_{feature}.gpkg'
            if not path.is_file():
                raise FileNotFoundError(path)
    for code in countries:
        for feature in features:
            process(code, feature, grids[code], block_size=args.block_size,
                    save_10m=args.save_10m, overwrite=args.overwrite)


if __name__ == '__main__':
    main()
