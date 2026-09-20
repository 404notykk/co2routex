#!/usr/bin/env python3
"""Prepare country polygons and average protected-area multipliers at 100 m.

Valid background = 1; IUCN II = 30; all other protected categories = 10.
Take the maximum at each location, then average by area within each cell's
country-boundary portion. Edge/point contact contributes zero area. The layer
stays in [1, 30]; other resistance layers multiply this completed layer later.
NL, DE, NO only; the existing country boundary and master mask define the domain.
Requires the adjacent, unchanged prepare_reference_grid.py from this project.

Factor reference: https://doi.org/10.1080/24725854.2025.2602823
This maximum/area-mean policy is a CO2RouteX adaptation, not an exact replication
of the authors' separate presence-based multiplication of protection classes.
"""

import argparse
import hashlib
import json
import re
import tempfile
import time
from collections import Counter
from pathlib import Path
from time import perf_counter

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import rasterio
from affine import Affine
from rasterio.features import rasterize
from rasterio.windows import bounds, transform as window_transform
import shapely

import prepare_reference_grid as reference

COUNTRY_ISO3 = {'NL': 'NLD', 'DE': 'DEU', 'NO': 'NOR'}
RAW_DIR = reference.PIPELINE_DIR / 'raw' / 'protected_areas'
PART_FOLDERS = tuple(f'WDPA_Jun2026_Public_shp_{i}' for i in range(3))
SOURCE_FILES = ()  # Explicit polygon shapefiles, absolute or relative to RAW_DIR.
PREP_CACHE_VERSION = 1
VECTOR_BATCH_SIZE = 4096


def file_signature(path):
    """Fast local-cache signature; include shapefile attributes and CRS sidecars."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    paths = [path]
    if path.suffix.lower() == '.shp':
        extensions = {'.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix', '.sbn', '.sbx'}
        paths = sorted(p for p in path.parent.iterdir()
                       if p.stem.lower() == path.stem.lower() and p.suffix.lower() in extensions)
        present = {p.suffix.lower() for p in paths}
        if not {'.shp', '.shx', '.dbf'} <= present:
            raise ValueError(f'{path}: missing SHP, SHX or DBF component')
    return [{'path': str(p), 'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
            for p in paths]


def find_sources():
    if SOURCE_FILES:
        sources = [Path(p) if Path(p).is_absolute() else RAW_DIR / p for p in SOURCE_FILES]
    else:
        sources = []
        for folder_name in PART_FOLDERS:
            folder = RAW_DIR / folder_name
            matches = sorted(p for p in folder.rglob('*')
                             if p.suffix.lower() == '.shp' and 'polygon' in p.stem.lower())
            if len(matches) != 1:
                raise ValueError(f'Expected one polygon shapefile in {folder}; found {matches}. '
                                 'Set SOURCE_FILES explicitly if necessary.')
            sources.append(matches[0])
    sources = [p.resolve() for p in sources]
    if not sources or len(set(sources)) != len(sources):
        raise ValueError('Expected distinct polygon source files')
    for path in sources:
        if path.suffix.lower() != '.shp':
            raise ValueError(f'Expected polygon shapefile: {path}')
        file_signature(path)
    return sources


def _write_json_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='json_', dir=path.parent) as temp:
        staged = Path(temp) / path.name
        staged.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        staged.replace(path)


def _read_json_if_valid(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _country_tokens(value):
    # Supports NLD, DEU;NOR, and concatenated ISO3 strings. Spatial selection is
    # also performed, so malformed/missing country attributes cannot remove land.
    if pd.isna(value):
        return set()
    return set(re.findall(r'[A-Z]{3}', str(value).strip().upper()))


def inspect_source(source, output_dir, force=False):
    """Inspect attributes without constructing geometries; cache audit and FIDs."""
    source = Path(source).resolve()
    signature = file_signature(source)
    token = hashlib.sha256(str(source).encode()).hexdigest()[:12]
    audit_path = Path(output_dir) / 'source_audit' / f'{source.stem}_{token}.json'
    key = {'version': PREP_CACHE_VERSION, 'source_signature': signature}
    cached = _read_json_if_valid(audit_path)
    if not force and isinstance(cached, dict) and cached.get('key') == key:
        print(f'  Attribute audit cache: {source.name}', flush=True)
        return cached['audit']
    print(f'  Inspecting attributes: {source.name}', flush=True)
    started = time.perf_counter()
    info = pyogrio.read_info(source)
    names = {str(field).upper(): str(field) for field in info['fields']}
    if not info['crs'] or 'IUCN_CAT' not in names:
        raise ValueError(f'{source}: missing CRS or IUCN_CAT field')
    countries = [names[field] for field in ('ISO3', 'PARENT_ISO3') if field in names]
    category = names['IUCN_CAT']
    site_fields = [names[field] for field in ('WDPAID', 'WDPA_PID', 'SITE_ID') if field in names]
    marine_field = names.get('MARINE')
    columns = list(dict.fromkeys([category] + countries + site_fields +
                                ([marine_field] if marine_field else [])))
    table = pyogrio.read_dataframe(source, columns=columns, read_geometry=False,
                                  fid_as_index=True, use_arrow=False)
    selected = {code: [] for code in COUNTRY_ISO3}
    country_counts, multiple, missing = Counter(), 0, 0
    if countries:
        rows = table[countries].itertuples(index=False, name=None)
        for fid, values in zip(table.index, rows):
            tokens = set().union(*(_country_tokens(value) for value in values))
            country_counts.update(tokens)
            multiple += len(tokens) > 1
            missing += not tokens
            for code, iso3 in COUNTRY_ISO3.items():
                if iso3 in tokens:
                    selected[code].append(int(fid))
    else:
        missing = len(table)
    audit = {
        'sourcepath': str(source), 'source_signature': signature,
        'crs': str(info['crs']), 'geometry_type': str(info['geometry_type']),
        'fields': [str(f) for f in info['fields']], 'attribute_rows': len(table),
        'countryfield': countries[0] if countries else None, 'countryfields': countries,
        'categoryfield': category, 'site_fields': site_fields, 'marine_field': marine_field,
        'category_counts': {str(k): int(v) for k, v in table[category].fillna('<missing>').value_counts().items()},
        'country_token_counts': dict(sorted(country_counts.items())),
        'multi_country_rows': int(multiple), 'missing_country_rows': int(missing),
        'marine_counts': ({str(k): int(v) for k, v in table[marine_field].fillna('<missing>').value_counts().items()}
                          if marine_field else {}),
        'target_match_counts': {code: len(fids) for code, fids in selected.items()},
        'target_fids': selected, 'inspection_seconds': time.perf_counter() - started,
        'selection_note': 'Country attributes prioritise reads; spatial safety selection also retains unmatched bbox candidates.'
    }
    if file_signature(source) != signature:
        raise RuntimeError(f'{source}: source changed during attribute inspection; retry')
    _write_json_atomic(audit_path, {'key': key, 'audit': audit})
    print(f'    {len(table):,} rows; country fields={countries or "none"}; '
          f'IUCN categories={audit["category_counts"]}; matches={audit["target_match_counts"]}; '
          f'{audit["inspection_seconds"]:.1f} s', flush=True)
    return audit


def inspect_sources(sources, output_dir, force=False):
    return [inspect_source(source, output_dir, force=force) for source in sources]


def _polygon_parts(geometry):
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry]
    if geometry.geom_type in ('MultiPolygon', 'GeometryCollection'):
        return [part for child in geometry.geoms for part in _polygon_parts(child)]
    return []


def _valid_polygon(geometry, context):
    if geometry is None or geometry.is_empty:
        raise ValueError(f'{context}: missing or empty protected-area geometry')
    if geometry.geom_type not in ('Polygon', 'MultiPolygon', 'GeometryCollection'):
        raise ValueError(f'{context}: expected polygon, found {geometry.geom_type}')
    repaired = int(not geometry.is_valid)
    if repaired:
        geometry = shapely.make_valid(geometry)
    if geometry.geom_type not in ('Polygon', 'MultiPolygon'):
        parts = _polygon_parts(geometry)
        geometry = shapely.union_all(parts) if parts else None
    if geometry is None or geometry.is_empty or geometry.area <= 0 or not geometry.is_valid:
        raise ValueError(f'{context}: no valid polygon area remains after repair')
    return geometry, repaired


def prepare_country_vectors(code, sources, output_dir, force=False):
    """Reuse or build one clipped, undissolved country layer with factors 10/30."""
    if code not in COUNTRY_ISO3:
        raise ValueError(f'Unsupported country: {code}')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = [Path(p).resolve() for p in sources]
    boundary_path = Path(reference.boundary_path(code))
    key = {'version': PREP_CACHE_VERSION, 'country': code, 'crs': 'EPSG:3035',
           'sources': [file_signature(p) for p in sources],
           'boundary': file_signature(boundary_path),
           'classification': 'IUCN_CAT II:30; all other polygon categories:10',
           'selection': 'attributes OR spatial bbox; exact terrestrial boundary clip; no coastal buffer',
           'overlap': 'retain polygons and use maximum when rasterising'}
    target = output_dir / f'{code}_protected_area_polygons.gpkg'
    manifest_path = output_dir / f'{code}_protected_area_polygons.manifest.json'
    manifest = _read_json_if_valid(manifest_path)
    if (not force and target.is_file() and isinstance(manifest, dict)
            and manifest.get('key') == key and manifest.get('output_signature') == file_signature(target)):
        print(f'{code}: using prepared polygon cache', flush=True)
        frame = pyogrio.read_dataframe(target, layer='protected_areas')
        return frame, manifest['report'], True
    started = time.perf_counter()
    boundary = reference.read_boundary(boundary_path).to_crs('EPSG:3035')
    boundary_geometry = shapely.union_all(boundary.geometry.to_numpy())
    boundary_geometry, _ = _valid_polygon(boundary_geometry, f'{code} boundary')
    shapely.prepare(boundary_geometry)
    records, source_reports = [], []
    for source in sources:
        audit = inspect_source(source, output_dir)  # Source signatures invalidate stale audits.
        category = audit['categoryfield']
        columns = list(dict.fromkeys([category] + audit['countryfields'] + audit['site_fields']))
        bbox = tuple(boundary.to_crs(audit['crs']).total_bounds)
        # These candidates also catch missing/miscoded ISO3 and transboundary
        # polygons. Bbox selection is conservative; exact clipping follows.
        candidates = pyogrio.read_dataframe(source, bbox=bbox, columns=[category],
                                            read_geometry=False, fid_as_index=True, use_arrow=False)
        candidate_set = set(int(fid) for fid in candidates.index)
        matched = [fid for fid in audit['target_fids'][code] if fid in candidate_set]
        matched_set = set(matched)
        safety = [int(fid) for fid in candidates.index if int(fid) not in matched_set]
        groups = [('attribute', matched), ('spatial_safety', safety)]
        report = {'source': str(source), 'attribute_matches': len(matched),
                  'bbox_candidates': len(candidates), 'spatial_safety_candidates': len(safety),
                  'retained_attribute': 0, 'retained_spatial_safety': 0,
                  'geometry_repairs': 0, 'fully_inside': 0, 'clipped': 0}
        print(f'{code}: {source.name}: {len(matched):,} attribute matches; '
              f'{len(safety):,} unmatched spatial candidates', flush=True)
        for mode, fids in groups:
            for start in range(0, len(fids), VECTOR_BATCH_SIZE):
                wanted = fids[start:start + VECTOR_BATCH_SIZE]
                frame = pyogrio.read_dataframe(source, fids=wanted, columns=columns,
                                               fid_as_index=True, use_arrow=False)
                if set(int(fid) for fid in frame.index) != set(wanted):
                    raise RuntimeError(f'{source}: could not retrieve every requested FID')
                fixed = []
                for fid, geom in zip(frame.index, frame.geometry):
                    geom, repairs = _valid_polygon(geom, f'{source.name}, FID {fid}')
                    fixed.append(geom)
                    report['geometry_repairs'] += repairs
                frame = frame.set_geometry(fixed).to_crs('EPSG:3035')
                for fid, row in frame.iterrows():
                    context = f'{source.name}, FID {fid}'
                    geometry, repairs = _valid_polygon(row.geometry, context)
                    report['geometry_repairs'] += repairs
                    if not boundary_geometry.intersects(geometry):
                        continue
                    if boundary_geometry.covers(geometry):
                        report['fully_inside'] += 1
                    else:
                        parts = _polygon_parts(geometry.intersection(boundary_geometry))
                        if not parts:
                            continue  # Boundary-only intersection has no polygon area.
                        geometry = parts[0] if len(parts) == 1 else shapely.union_all(parts)
                        if geometry.is_empty or geometry.area <= 0:
                            continue
                        geometry, repairs = _valid_polygon(geometry, context)
                        report['geometry_repairs'] += repairs
                        report['clipped'] += 1
                    raw_category = '' if row[category] is None else str(row[category]).strip()
                    record = {'geometry': geometry, 'multiplier': 30 if raw_category.upper() == 'II' else 10,
                              'iucn_cat': raw_category, 'source_part': source.stem, 'source_fid': int(fid)}
                    for field in audit['countryfields'] + audit['site_fields']:
                        value = row[field]
                        record[field.lower()] = None if value is None else str(value)
                    records.append(record)
                    report[f'retained_{mode}'] += 1
                print(f'    {mode}: {min(start + VECTOR_BATCH_SIZE, len(fids)):,}/{len(fids):,} read', flush=True)
        source_reports.append(report)
    if not records:
        raise ValueError(f'{code}: no protected-area polygon area inside the boundary; check sources')
    frame = gpd.GeoDataFrame(records, geometry='geometry', crs='EPSG:3035')
    # Separate distant islands/parts so their combined bbox does not select
    # a large multipart geometry for every intervening processing block.
    frame = frame.explode(index_parts=False, ignore_index=True)
    frame['multiplier'] = frame['multiplier'].astype('uint8')
    report = {'country': code, 'features': len(frame), 'source_records_retained': len(records),
              'sources': source_reports,
              'multiplier_counts': {str(k): int(v) for k, v in frame['multiplier'].value_counts().items()},
              'preparation_seconds': time.perf_counter() - started, 'cache_path': str(target),
              'marine_policy': 'Do not exclude by MARINE attribute; clip all selected polygons to terrestrial boundary.'}
    if [file_signature(p) for p in sources] != key['sources'] or file_signature(boundary_path) != key['boundary']:
        raise RuntimeError('Inputs changed during extraction; prepared cache not saved. Retry.')
    with tempfile.TemporaryDirectory(prefix=f'{code}_vectors_', dir=output_dir) as temp:
        staged = Path(temp) / target.name
        pyogrio.write_dataframe(frame, staged, layer='protected_areas', driver='GPKG',
                                geometry_type='MultiPolygon', promote_to_multi=True,
                                layer_options={'SPATIAL_INDEX': 'YES'})
        staged.replace(target)
    # A crash before the manifest is replaced leaves a signature mismatch, which
    # prevents the incomplete cache update from being reused next time.
    _write_json_atomic(manifest_path, {'key': key, 'output_signature': file_signature(target), 'report': report})
    print(f'{code}: cached {len(frame):,} polygons in {target.name}; '
          f'{time.perf_counter() - started:.1f} s total preparation', flush=True)
    return frame, report, False

COUNTRIES = tuple(COUNTRY_ISO3)


BLOCK_SIZE = 128                 # 100 m cells per block edge; no fine grid.
GEOMETRY_BATCH = 8192            # Bounds polygon-clipping working arrays.
QUERY_CELLS = 512                # Bounds each exact cell-area calculation.
BACKGROUND = 1
NODATA = -9999.0
RASTER_VERSION = 3               # Invalidate categorical-MAX rasters, keep vectors.
AREA_TOLERANCE_M2 = 1e-6         # Rounding tolerance, not a minimum polygon area.


def load_grid(code):
    """Use exactly the same reference JSON contract as the previous script."""
    path = reference.REFERENCE_DIR / f'{code}_reference_grid.json'
    grid = json.loads(path.read_text())
    if grid.get('schema_version') != 1 or grid.get('country') != code:
        raise ValueError(f'{code}: unsupported or mismatched reference JSON: {path}')
    for label, input_path in [('population', reference.POPULATION_PATH),
                              ('boundary', reference.boundary_path(code))]:
        if grid[f'{label}_signature'] != reference.fingerprint(input_path):
            raise ValueError(f'{code}: {label} changed; regenerate the reference grid')
    return grid


def validate_grid(mask, grid):
    transform = Affine(*grid['transform'])
    if not np.allclose([transform.a, transform.b, transform.d, transform.e],
                       [100, 0, 0, -100], atol=1e-8, rtol=0):
        raise ValueError('Expected a north-up 100 m reference grid')
    if (mask.crs != rasterio.crs.CRS.from_epsg(3035)
            or rasterio.crs.CRS.from_string(grid['crs']) != mask.crs
            or (mask.width, mask.height) != (grid['width'], grid['height'])
            or mask.count != 1 or mask.nodata != 0
            or not np.allclose(list(mask.transform)[:6], list(transform)[:6],
                               atol=1e-7, rtol=0)):
        raise ValueError('Population-valid mask differs from the reference grid')
    return transform


def _polygon_union(geometries):
    """Union polygon area only, dropping line/point remnants after clipping."""
    parts = [part for geometry in geometries for part in _polygon_parts(geometry)]
    return shapely.union_all(parts) if parts else shapely.GeometryCollection()


def _local_union(geometries, domain):
    """Clip before union so detailed geometry outside this block is not merged."""
    if not len(geometries) or domain.is_empty:
        return shapely.GeometryCollection()
    # A single covering polygon already supplies the entire local domain.
    if np.any(shapely.covers(geometries, domain)):
        return domain
    batches = []
    for start in range(0, len(geometries), GEOMETRY_BATCH):
        clipped = shapely.intersection(geometries[start:start + GEOMETRY_BATCH], domain)
        batches.append(_polygon_union(clipped))
    return _polygon_union(batches)


def _coverage_area(geometry, shape, transform, included):
    """Exact union coverage in m2; rasterise interiors, intersect boundary cells.

All-touched rasterisation only identifies interiors and candidate boundary
cells. It does not set the final area of a partially covered cell.
"""
    result = np.zeros(shape, dtype='float64')
    if geometry.is_empty or not included.any():
        return result
    covered = rasterize([(geometry, 1)], out_shape=shape, transform=transform,
                        fill=0, dtype='uint8', all_touched=True).astype(bool)
    cell_area = abs(transform.a * transform.e)
    result[covered & included] = cell_area
    edges = rasterize([(geometry.boundary, 1)], out_shape=shape,
                      transform=transform, fill=0, dtype='uint8', all_touched=True)
    rows, cols = np.nonzero((edges != 0) & included)
    for start in range(0, len(rows), QUERY_CELLS):
        rr = rows[start:start + QUERY_CELLS]
        cc = cols[start:start + QUERY_CELLS]
        x = transform.c + cc * transform.a
        y = transform.f + rr * transform.e
        cells = shapely.box(x, y + transform.e, x + transform.a, y)
        result[rr, cc] = shapely.area(shapely.intersection(cells, geometry))
    tolerance = max(AREA_TOLERANCE_M2, cell_area * 1e-9)
    if (not np.isfinite(result).all() or np.any(result < -tolerance)
            or np.any(result > cell_area + tolerance)):
        raise ValueError('Invalid polygon-cell intersection area')
    return np.clip(result, 0, cell_area)


def rasterise_block(geometries, values, shape, transform, domain_geometry=None,
                    included=None, return_areas=False):
    """Area mean of the pointwise maximum (1, 10, 30), capped at 30.

The denominator is cell intersection with domain_geometry (the country
boundary in production), or the full cell if no domain is supplied. Areas
outside included cells are zero; excluded or zero-domain cells are NoData.
With return_areas, return (result, domain_area, protected_any_area, II_area).
"""
    geometries = np.asarray(geometries, dtype=object)
    values = np.asarray(values)
    if len(geometries) != len(values) or np.any(~np.isin(values, [10, 30])):
        raise ValueError('Expected one multiplier (10 or 30) per polygon')
    if transform.a <= 0 or transform.e >= 0 or transform.b != 0 or transform.d != 0:
        raise ValueError('Expected a north-up grid for area calculations')
    included = np.ones(shape, dtype=bool) if included is None else np.asarray(included, dtype=bool)
    if included.shape != tuple(shape):
        raise ValueError('Included-cell mask shape differs from the processing block')
    footprint = shapely.box(transform.c, transform.f + shape[0] * transform.e,
                            transform.c + shape[1] * transform.a, transform.f)
    if domain_geometry is None or shapely.covers(domain_geometry, footprint):
        domain = footprint
        domain_area = np.where(included, abs(transform.a * transform.e), 0.0)
    else:
        domain = _polygon_union([shapely.intersection(domain_geometry, footprint)])
        domain_area = _coverage_area(domain, shape, transform, included)
    result = np.full(shape, NODATA, dtype='float64')
    valid = included & (domain_area > 0)
    any_area = np.zeros(shape, dtype='float64')
    ii_area = np.zeros(shape, dtype='float64')
    if valid.any():
        ii_union = _local_union(geometries[values == 30], domain)
        if shapely.covers(ii_union, domain):
            # Every included location is already 30; class 10 cannot change it.
            any_area = domain_area.copy()
            ii_area = domain_area.copy()
        else:
            other_union = _local_union(geometries[values == 10], domain)
            any_union = _polygon_union([ii_union, other_union])
            any_area = _coverage_area(any_union, shape, transform, valid)
            ii_area = _coverage_area(ii_union, shape, transform, valid)
        tolerance = np.maximum(AREA_TOLERANCE_M2, domain_area * 1e-9)
        if (np.any(any_area > domain_area + tolerance)
                or np.any(ii_area > any_area + tolerance)):
            raise ValueError('Protected union area exceeds its containing domain')
        any_area = np.minimum(any_area, domain_area)
        ii_area = np.minimum(ii_area, any_area)
        # II is included in any_area: 1 + 9 + 20 = 30, never 30 * 10.
        result[valid] = (BACKGROUND + 9 * (any_area[valid] / domain_area[valid])
                         + 20 * (ii_area[valid] / domain_area[valid]))
        result[valid] = np.clip(result[valid], BACKGROUND, 30)
    if return_areas:
        return result, domain_area, any_area, ii_area
    return result


def rasterise_country(code, frame, vector_report, cache_hit, output_dir,
                      block_size=BLOCK_SIZE, overwrite=False):
    if block_size <= 0:
        raise ValueError('Block size must be positive')
    output_dir = Path(output_dir)
    grid = load_grid(code)
    grid_path = reference.REFERENCE_DIR / f'{code}_reference_grid.json'
    mask_path = reference.REFERENCE_DIR / grid['population_valid_mask']
    boundary_path = Path(reference.boundary_path(code))
    vector_path = output_dir / f'{code}_protected_area_polygons.gpkg'
    output_path = output_dir / f'{code}_protected_area_resistance_100m.tif'
    report_path = output_dir / f'{code}_protected_area_resistance.json'
    key = dict(version=RASTER_VERSION, vectors=file_signature(vector_path),
               grid=file_signature(grid_path), mask=file_signature(mask_path),
               boundary=file_signature(boundary_path),
               background=BACKGROUND, category_II=30, other_categories=10,
               coverage='exact-area-weighted', overlap='pointwise-maximum',
               denominator='cell-intersection-country-boundary',
               area_tolerance_m2=AREA_TOLERANCE_M2, nodata=NODATA)
    if not overwrite and output_path.exists() and report_path.exists():
        try:
            old = json.loads(report_path.read_text())
            if (old.get('cache_key') == key
                    and old.get('raster_signature') == file_signature(output_path)):
                print(f'{code}: reusing current 100 m raster: {output_path}', flush=True)
                return
        except (ValueError, OSError):
            pass

    if frame.crs != rasterio.crs.CRS.from_epsg(3035):
        raise ValueError(f'{code}: prepared polygons must be in EPSG:3035')
    boundary = reference.read_boundary(boundary_path).to_crs('EPSG:3035')
    domain_geometry, _ = _valid_polygon(shapely.union_all(boundary.geometry.to_numpy()),
                                        f'{code} boundary')
    shapely.prepare(domain_geometry)
    geometries = frame.geometry.to_numpy()
    values = frame['multiplier'].to_numpy()
    if np.any(~np.isin(values, [10, 30])):
        raise ValueError(f'{code}: prepared polygons contain an invalid multiplier')
    tree = shapely.STRtree(geometries)
    width, height = grid['width'], grid['height']
    mask_valid_cells = valid_cells = zero_domain_cells = partial_domain_cells = 0
    minimum, maximum, value_sum = float('inf'), float('-inf'), 0.0
    area_totals = dict(domain=0.0, protected_any=0.0, category_II=0.0,
                       other_protected_only=0.0, unprotected=0.0)
    total = ((width + block_size - 1) // block_size) * ((height + block_size - 1) // block_size)
    started = perf_counter()
    timings = dict(mask_read=0.0, polygon_lookup=0.0, rasterisation=0.0, write=0.0)
    print(f'{code}: averaging {len(geometries):,} polygon parts directly at 100 m '
          f'({width:,} x {height:,} cells; {total:,} blocks)', flush=True)
    with rasterio.open(mask_path) as mask:
        transform = validate_grid(mask, grid)
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f'{code}_protected_raster_', dir=output_dir) as tmp:
            staged = Path(tmp)
            with rasterio.open(staged / output_path.name, 'w', **reference.profile(
                    width, height, transform, 'float32', NODATA)) as dst:
                for number, win in enumerate(reference.windows(width, height, block_size), 1):
                    t = perf_counter()
                    raw_mask = mask.read(1, window=win)
                    if np.any((raw_mask != 0) & (raw_mask != 1)):
                        raise ValueError('Population-valid mask must contain only zero and one')
                    included = raw_mask == 1
                    mask_valid_cells += int(np.count_nonzero(included))
                    timings['mask_read'] += perf_counter() - t
                    saved = np.full(included.shape, NODATA, dtype='float32')
                    if included.any():
                        t = perf_counter()
                        indices = tree.query(shapely.box(*bounds(win, transform)), predicate='intersects')
                        timings['polygon_lookup'] += perf_counter() - t
                        t = perf_counter()
                        result, domain_area, any_area, ii_area = rasterise_block(
                            geometries[indices], values[indices], included.shape,
                            window_transform(win, transform), domain_geometry=domain_geometry,
                            included=included, return_areas=True)
                        timings['rasterisation'] += perf_counter() - t
                        valid = included & (domain_area > 0)
                        valid_cells += int(np.count_nonzero(valid))
                        zero_domain_cells += int(np.count_nonzero(included & ~valid))
                        full_cell_area = abs(transform.a * transform.e)
                        partial_domain_cells += int(np.count_nonzero(
                            valid & (domain_area < full_cell_area - AREA_TOLERANCE_M2)))
                        saved = result.astype('float32')
                        if valid.any():
                            samples = saved[valid]
                            if (not np.isfinite(samples).all() or np.any(samples < 1)
                                    or np.any(samples > 30)):
                                raise ValueError('Area-weighted multiplier is outside [1, 30]')
                            minimum = min(minimum, float(samples.min()))
                            maximum = max(maximum, float(samples.max()))
                            value_sum += float(samples.sum(dtype='float64'))
                        area_totals['domain'] += float(domain_area.sum())
                        area_totals['protected_any'] += float(any_area.sum())
                        area_totals['category_II'] += float(ii_area.sum())
                        area_totals['other_protected_only'] += float((any_area - ii_area).sum())
                        area_totals['unprotected'] += float((domain_area - any_area).sum())
                    t = perf_counter()
                    dst.write(saved, 1, window=win)
                    timings['write'] += perf_counter() - t
                    if number == 1 or number % 100 == 0 or number == total:
                        elapsed = perf_counter() - started
                        print(f'  {code}: blocks {number:,}/{total:,}; elapsed {elapsed:.1f} s', flush=True)
                if mask_valid_cells != grid['valid_population_cells']:
                    raise ValueError('Master-mask valid-cell count differs from the reference JSON')
                if valid_cells + zero_domain_cells != mask_valid_cells:
                    raise ValueError('Output/domain cell counts are inconsistent')
                dst.update_tags(method='direct 100 m exact area-weighted mean',
                                raster_version=RASTER_VERSION, category_II=30, other_categories=10,
                                background=BACKGROUND, maximum_multiplier=30,
                                coverage='union intersection area; edge/point contact contributes zero',
                                priority='maximum at each location, then area mean within cell',
                                denominator='cell intersection with supplied country boundary',
                                formula='1 + 9 * protected_any_fraction + 20 * category_II_fraction',
                                zero_domain='NoData', units='dimensionless')
            timings['total_raster_stage'] = perf_counter() - started
            # Do not label a mixed-input run as current if an input changed while reading.
            for label, path in [('vectors', vector_path), ('grid', grid_path),
                                ('mask', mask_path), ('boundary', boundary_path)]:
                if file_signature(path) != key[label]:
                    raise RuntimeError(f'{code}: {label} changed during rasterisation; retry')
            # Commit only after successful raster completion. A missing or stale
            # report invalidates reuse, so interruption between renames is safe.
            (staged / output_path.name).replace(output_path)
            report = dict(country=code, output=output_path.name, cache_key=key,
                          raster_signature=file_signature(output_path), grid=grid,
                          prepared_vectors=vector_path.name, vector_cache_hit=cache_hit,
                          vector_report=vector_report, background=BACKGROUND, nodata=NODATA,
                          valid_cells=valid_cells, mask_valid_cells=mask_valid_cells,
                          zero_domain_cells=zero_domain_cells, partial_domain_cells=partial_domain_cells,
                          statistics={'min': minimum if valid_cells else None,
                                      'max': maximum if valid_cells else None,
                                      'mean': value_sum / valid_cells if valid_cells else None,
                                      'mean_definition': 'arithmetic mean of valid saved cell values'},
                          area_totals_m2=area_totals, maximum_multiplier=30,
                          block_size=block_size, method='direct 100 m; area mean of pointwise maximum',
                          denominator='cell intersection with supplied country boundary',
                          fine_grid_used=False, timings_seconds=timings)
            staged_report = staged / report_path.name
            staged_report.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
            staged_report.replace(report_path)
    print(f'{code}: saved {output_path}; {valid_cells:,} valid cells; '
          f'{zero_domain_cells:,} master-mask cells with zero domain area; '
          f'raster stage {timings["total_raster_stage"]:.1f} s', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', nargs='+', choices=COUNTRIES, default=COUNTRIES)
    stages = parser.add_mutually_exclusive_group()
    stages.add_argument('--inspect-only', action='store_true', help='Inspect and cache source attributes only')
    stages.add_argument('--prepare-only', action='store_true', help='Prepare country vector caches without rasters')
    parser.add_argument('--rebuild-vectors', action='store_true', help='Rebuild source audits and country polygon caches')
    parser.add_argument('--overwrite', action='store_true', help='Regenerate rasters even when their cache is current')
    parser.add_argument('--block-size', type=int, default=BLOCK_SIZE, help='100 m cells per processing block edge')
    args = parser.parse_args()
    if args.block_size <= 0:
        parser.error('--block-size must be positive')
    if int(shapely.__version__.split('.')[0]) < 2:
        raise RuntimeError('Shapely 2 or newer is required')
    started = perf_counter()
    output_dir = reference.PIPELINE_DIR / 'intermediate' / 'protected_areas'
    sources = find_sources()
    inspect_sources(sources, output_dir, force=args.rebuild_vectors)
    if args.inspect_only:
        print(f'Attribute inspection finished in {perf_counter() - started:.1f} s', flush=True)
        return
    for code in dict.fromkeys(args.countries):
        frame, report, cache_hit = prepare_country_vectors(
            code, sources, output_dir, force=args.rebuild_vectors)
        if not args.prepare_only:
            rasterise_country(code, frame, report, cache_hit, output_dir,
                              block_size=args.block_size, overwrite=args.overwrite)
    print(f'Finished in {perf_counter() - started:.1f} s', flush=True)


if __name__ == '__main__':
    main()
