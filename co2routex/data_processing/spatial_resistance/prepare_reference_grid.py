#!/usr/bin/env python3
"""Define population-aligned 100 m master and 10 m intermediate country grids.

Dependencies: numpy, rasterio, geopandas, shapely, pyproj.
"""
import argparse
import json
import math
from pathlib import Path
import tempfile

import geopandas as gpd
import numpy as np
import rasterio
from affine import Affine
from rasterio.features import rasterize
from rasterio.windows import Window, transform as window_transform
from shapely.ops import unary_union
from shapely.validation import explain_validity, make_valid

SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SCRIPT_DIR.parents[1] / 'database' / 'pipeline'
POPULATION_PATH = PIPELINE_DIR / 'raw/population/JRC-ESTAT_Census_Population_2021_100m.tif'
BOUNDARY_DIR = PIPELINE_DIR / 'intermediate/boundaries'
REFERENCE_DIR = PIPELINE_DIR / 'intermediate/reference_grid'
COUNTRIES = ('NL', 'DE', 'NO')
CRS = 'EPSG:3035'
BLOCK_SIZE = 256  # Number of 100 m cells along one processing block edge.
FACTOR = 10


def fingerprint(path):
    """Record source bytes metadata; not a cryptographic checksum."""
    path = Path(path)
    paths = [path]
    if path.suffix.lower() == '.shp':
        paths = [path.with_suffix(s) for s in ('.shp', '.shx', '.dbf', '.prj')]
    return [{'name': p.name, 'size': p.stat().st_size,
             'mtime_ns': p.stat().st_mtime_ns} for p in paths]


def boundary_path(code):
    return BOUNDARY_DIR / f'{code}.shp'


def _polygon_parts(geometry):
    if geometry.geom_type == 'Polygon':
        return [geometry] if not geometry.is_empty else []
    if geometry.geom_type in ('MultiPolygon', 'GeometryCollection'):
        return [part for child in geometry.geoms for part in _polygon_parts(child)]
    return []


def _validate_boundary(frame, path, stage):
    """Repair polygon topology; reject missing/empty/non-polygon source rows."""
    repaired = frame.copy()
    for position in range(len(frame)):
        geometry = frame.geometry.iloc[position]
        label = f'{path}: feature row {position} ({stage})'
        if geometry is None:
            raise ValueError(f'{label}: missing geometry')
        if geometry.is_empty:
            raise ValueError(f'{label}: empty geometry')
        if geometry.geom_type not in ('Polygon', 'MultiPolygon'):
            raise ValueError(f'{label}: expected Polygon/MultiPolygon, found {geometry.geom_type}')
        if geometry.is_valid:
            continue
        reason = explain_validity(geometry)
        fixed = make_valid(geometry)
        parts = _polygon_parts(fixed)
        if not parts:
            raise ValueError(f'{label}: {reason}; repair produced no polygon area')
        polygon = unary_union(parts)
        if (polygon.is_empty or not polygon.is_valid
                or polygon.geom_type not in ('Polygon', 'MultiPolygon')):
            raise ValueError(f'{label}: {reason}; polygon repair failed')
        repaired.iat[position, repaired.columns.get_loc(repaired.geometry.name)] = polygon
        print(f'Boundary repair in memory: {label}: {reason}', flush=True)
        if fixed.geom_type == 'GeometryCollection':
            print('  Retained all polygon parts; lower-dimensional remnants do not define mask area.', flush=True)
    return repaired


def read_boundary(path):
    frame = gpd.read_file(path)
    if frame.crs is None:
        raise ValueError(f'Missing CRS: {path}')
    if frame.empty:
        raise ValueError(f'Boundary file contains no features: {path}')
    frame = _validate_boundary(frame, path, 'source')
    return _validate_boundary(frame.to_crs(CRS), path, 'EPSG:3035')


def windows(width, height, size):
    for row in range(0, height, size):
        for col in range(0, width, size):
            yield Window(col, row, min(size, width-col), min(size, height-row))


def polygon_mask(frame, transform, shape):
    """Country inclusion uses cell centres; no buffering."""
    return rasterize(((g, 1) for g in frame.geometry), out_shape=shape,
                     transform=transform, fill=0, dtype='uint8', all_touched=False).astype(bool)


def profile(width, height, transform, dtype='uint8', nodata=0):
    return dict(driver='GTiff', width=width, height=height, count=1,
                crs=CRS, transform=transform, dtype=dtype, nodata=nodata,
                tiled=True, blockxsize=256, blockysize=256,
                compress='deflate', BIGTIFF='IF_SAFER')


def grid_window(src, bounds):
    t = src.transform
    if (src.crs != rasterio.crs.CRS.from_string(CRS)
            or not np.allclose([t.a, t.b, t.d, t.e], [100, 0, 0, -100], atol=1e-8, rtol=0)):
        raise ValueError('Population source must be north-up EPSG:3035 with 100 m cells; no resampling is performed.')
    xmin, ymin, xmax, ymax = bounds
    c0 = max(0, math.floor((xmin-t.c)/100))
    c1 = min(src.width, math.ceil((xmax-t.c)/100))
    r0 = max(0, math.floor((t.f-ymax)/100))
    r1 = min(src.height, math.ceil((t.f-ymin)/100))
    if c1 <= c0 or r1 <= r0:
        raise ValueError('Country does not overlap population raster extent.')
    return Window(c0, r0, c1-c0, r1-r0)


def prepare_country(code, block_size=BLOCK_SIZE):
    boundary = read_boundary(boundary_path(code))
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    with rasterio.open(POPULATION_PATH) as src:
        win = grid_window(src, boundary.total_bounds)
        width, height = int(win.width), int(win.height)
        transform = src.window_transform(win)
        # Subdividing each 100 m cell yields exactly nested 10 m cells.
        fine = transform * Affine.scale(1/FACTOR)
        mask_name = f'{code}_population_valid_mask_100m.tif'
        valid_count = 0
        with tempfile.TemporaryDirectory(prefix=f'{code}_', dir=REFERENCE_DIR) as tmp:
            mask_file = Path(tmp) / mask_name
            with rasterio.open(mask_file, 'w', **profile(width, height, transform)) as dst:
                for local in windows(width, height, block_size):
                    source_win = Window(win.col_off+local.col_off, win.row_off+local.row_off,
                                        local.width, local.height)
                    pop = src.read(1, window=source_win, masked=True)
                    inside = polygon_mask(boundary, window_transform(local, transform), pop.shape)
                    valid = inside & ~np.ma.getmaskarray(pop) & np.isfinite(pop.data)
                    if np.any(valid & (pop.data < 0)):
                        raise ValueError('Unmasked negative population values found; inspect source NoData definition.')
                    valid_count += int(valid.sum())
                    dst.write(valid.astype('uint8'), 1, window=local)
                dst.update_tags(description='1 = valid population cell inside country; 0 = outside or missing',
                                population_units='people per 100 m cell (one hectare)')
            if not valid_count:
                raise ValueError(f'No valid population cells for {code}')
            grid = dict(schema_version=1, country=code, crs=CRS,
                        width=width, height=height, transform=list(transform)[:6],
                        fine_width=width*FACTOR, fine_height=height*FACTOR,
                        fine_transform=list(fine)[:6], factor=FACTOR,
                        population_window=[int(win.col_off), int(win.row_off), width, height],
                        population_file=str(POPULATION_PATH), population_signature=fingerprint(POPULATION_PATH),
                        boundary_file=str(boundary_path(code)), boundary_signature=fingerprint(boundary_path(code)),
                        population_valid_mask=mask_name, valid_population_cells=valid_count,
                        extent_policy='Country bounds rounded outward to source 100 m grid, intersected with source extent',
                        mask_policy='Polygon cell centres; population NoData retained; zero population is valid')
            staged_json = Path(tmp) / f'{code}_reference_grid.json'
            staged_json.write_text(json.dumps(grid, indent=2)+'\n')
            mask_file.replace(REFERENCE_DIR / mask_name)
            staged_json.replace(REFERENCE_DIR / staged_json.name)
        print(f'{code}: {width} x {height} at 100 m; nested 10 m grid defined; {valid_count:,} valid population cells')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', nargs='+', default=COUNTRIES)
    args = parser.parse_args()
    fingerprint(POPULATION_PATH)
    for code in args.countries:
        fingerprint(boundary_path(code))
    for code in args.countries:
        prepare_country(code)


if __name__ == '__main__':
    main()
