#!/usr/bin/env python3
"""Prepare a vector water-only extension outside a country boundary.

Domain preparation only: no resistance rasters or multipliers are generated.
Install in data_processing/spatial_resistance/water_buffer/. Python >=3.10;
dependencies: geopandas, shapely>=2.1, pyproj, pyarrow, matplotlib, numpy, pandas, pyogrio.
Automatic download additionally uses overturemaps==1.0.2.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import MultiPolygon, box
from shapely.ops import unary_union

SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SCRIPT_DIR.parents[2] / 'database' / 'pipeline'
COUNTRIES = ('NL', 'DE', 'NO')
TARGET_CRS = CRS.from_epsg(3035)
WATER_SUBTYPES = frozenset(('canal', 'human_made', 'lake', 'ocean', 'pond',
                           'reservoir', 'river', 'spring', 'stream', 'water'))
SURFACE_CLASSES = frozenset(('basin', 'canal', 'ditch', 'dock', 'drain', 'fairway',
                            'fish_pass', 'fishpond', 'lagoon', 'lake', 'moat', 'ocean',
                            'oxbow', 'pond', 'reflecting_pool', 'reservoir', 'river',
                            'salt_pond', 'sea', 'stream', 'tidal_channel', 'water',
                            'water_storage'))
EXCLUDED_FACILITY_CODES = frozenset(('swimming_pool', 'wastewater', 'sewage'))
EXCLUDED_SOURCE_TAGS = {'leisure': frozenset(('swimming_pool',)),
                        'water': frozenset(('wastewater', 'sewage')),
                        'basin': frozenset(('wastewater', 'sewage')),
                        'reservoir_type': frozenset(('wastewater', 'sewage'))}
CAVEAT = ('Outside mapped water means not selected as water, not proven land. '
          'Vector source completeness is not independently established; this is '
          'not an LCM classification-coverage report.')


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('must be finite and greater than zero')
    return number


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', nargs='+', choices=COUNTRIES, default=list(COUNTRIES))
    parser.add_argument('--pipeline-dir', type=Path, default=PIPELINE_DIR)
    parser.add_argument('--boundary-dir', type=Path)
    parser.add_argument('--reference-dir', type=Path)
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--buffer-km', type=positive_float, default=5.0)
    parser.add_argument('--water-file', type=Path,
                        help='existing Overture water GeoParquet, for one country only')
    parser.add_argument('--release', default='latest',
                        help='Overture release (default: latest, resolved once per run)')
    parser.add_argument('--include-intermittent', action='store_true',
                        help='also select explicitly intermittent water (excluded by default)')
    return parser


def file_metadata(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    stat = path.stat()
    return dict(path=str(path.resolve()), size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns, sha256=digest.hexdigest())


def polygon_parts(geometry):
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry]
    if geometry.geom_type in ('MultiPolygon', 'GeometryCollection'):
        return [p for child in geometry.geoms for p in polygon_parts(child)]
    return []


def polygons(geometry):
    return unary_union(polygon_parts(geometry))


def repair_polygon(geometry):
    if geometry is None or geometry.is_empty:
        raise ValueError('Missing or empty polygon geometry')
    if geometry.geom_type not in ('Polygon', 'MultiPolygon'):
        raise ValueError(f'Expected a polygon; found {geometry.geom_type}')
    repaired = not geometry.is_valid
    result = polygons(make_valid(geometry)) if repaired else geometry
    if result.is_empty or not result.is_valid or not np.isfinite(result.bounds).all():
        raise ValueError('Polygon repair did not produce valid finite polygon area')
    return result, repaired


def read_boundary(path):
    frame = gpd.read_file(path)
    if frame.crs is None or frame.empty:
        raise ValueError(f'Boundary must contain polygons and have a CRS: {path}')
    repairs = 0
    for stage in ('source', 'EPSG:3035'):
        if stage != 'source':
            frame = frame.to_crs(TARGET_CRS)
        geometries = []
        for position, geometry in enumerate(frame.geometry):
            try:
                geometry, changed = repair_polygon(geometry)
            except ValueError as exc:
                raise ValueError(f'{path}, row {position}, {stage}: {exc}') from exc
            geometries.append(geometry)
            repairs += int(changed)
        frame = frame.set_geometry(geometries)
    return repair_polygon(unary_union(frame.geometry))[0], repairs


def load_reference(path, code):
    grid = json.loads(path.read_text())
    if grid.get('schema_version') != 1 or grid.get('country') != code:
        raise ValueError(f'Expected schema 1 reference grid for {code}: {path}')
    if CRS.from_user_input(grid['crs']) != TARGET_CRS:
        raise ValueError(f'Reference grid must use EPSG:3035: {path}')
    transform = np.asarray(grid['transform'], dtype=float)
    fine = np.asarray(grid['fine_transform'], dtype=float)
    if (transform.shape != (6,) or not np.isfinite(transform).all()
            or not np.allclose(transform[[0, 1, 3, 4]], [100, 0, 0, -100],
                               atol=1e-8, rtol=0)):
        raise ValueError(f'Reference must be north-up with 100 m cells: {path}')
    expected = transform.copy()
    expected[[0, 1, 3, 4]] /= 10
    if (grid.get('factor') != 10 or fine.shape != (6,)
            or not np.isfinite(fine).all()
            or not np.allclose(fine, expected, atol=1e-7, rtol=0)):
        raise ValueError(f'Reference 10 m grid must nest exactly: {path}')
    for name in ('width', 'height'):
        n = grid[name]
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
            raise ValueError(f'Invalid reference {name}: {path}')
        if grid['fine_' + name] != 10 * n:
            raise ValueError(f'Invalid reference fine_{name}: {path}')
    return grid


def expanded_grid(reference, bounds):
    xmin, ymin, xmax, ymax = bounds
    anchor_x, anchor_y = reference['transform'][2], reference['transform'][5]
    c0, c1 = math.floor((xmin-anchor_x)/100), math.ceil((xmax-anchor_x)/100)
    r0, r1 = math.floor((anchor_y-ymax)/100), math.ceil((anchor_y-ymin)/100)
    width, height = c1-c0, r1-r0
    if width <= 0 or height <= 0:
        raise ValueError('Expanded grid has no cells')
    x, y = anchor_x+c0*100, anchor_y-r0*100
    return dict(crs='EPSG:3035', width=width, height=height,
                transform=[100, 0, x, 0, -100, y], factor=10,
                fine_width=width*10, fine_height=height*10,
                fine_transform=[10, 0, x, 0, -10, y],
                reference_column_offset=c0, reference_row_offset=r0,
                extent_policy='Full outer strip bounds rounded outward on reference lattice',
                rasterisation_status='Metadata only; no raster or cell inclusion rule applied')


def download_source(args, code, bbox):
    """Download/cache an Overture water extract selected by an automatic bbox."""
    import hashlib
    import importlib.metadata
    import re
    import time
    from datetime import datetime, timezone
    import pyarrow.parquet as pq

    # Resolve once so every country in this run uses the same release.
    release = getattr(args, '_resolved_overture_release', None)
    if release is None:
        requested = args.release or 'latest'
        if requested == 'latest':
            from urllib.request import urlopen
            try:
                with urlopen('https://stac.overturemaps.org/catalog.json', timeout=30) as response:
                    release = json.load(response)['latest']
            except Exception as exc:
                raise RuntimeError(
                    'Cannot resolve the Overture release. Check internet access, or use '
                    '--release with a previously cached release, or --water-file.'
                ) from exc
        else:
            release = requested
        if not isinstance(release, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.\d+', release):
            raise ValueError(f'Invalid Overture release: {release!r}')
        args._resolved_overture_release = release

    # Round OUTWARD, never trim source coverage while constructing the cache key.
    query_bbox = [math.floor(float(bbox[0]) * 1e6) / 1e6,
                  math.floor(float(bbox[1]) * 1e6) / 1e6,
                  math.ceil(float(bbox[2]) * 1e6) / 1e6,
                  math.ceil(float(bbox[3]) * 1e6) / 1e6]
    request = {'release': release, 'type': 'water', 'bbox_wgs84': query_bbox}
    key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:16]
    cache_dir = args.pipeline_dir / 'raw' / 'water_buffer' / 'overture' / release
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f'{code}_water_{key}.parquet'
    manifest_path = cache_dir / f'{code}_water_{key}.source.json'

    def file_hash(path):
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    if cache_path.exists() or manifest_path.exists():
        if not (cache_path.is_file() and manifest_path.is_file()):
            raise ValueError(f'Incomplete source cache: {cache_path}. Remove the incomplete '
                             'cache pair and rerun, or supply --water-file.')
        cached = json.loads(manifest_path.read_text())
        if cached.get('request') != request or cached.get('sha256') != file_hash(cache_path):
            raise ValueError(f'Source cache metadata/checksum mismatch: {cache_path}. '
                             'Inspect the cached files before reusing them.')
        if pq.ParquetFile(cache_path).metadata.num_rows != cached.get('rows'):
            raise ValueError(f'Source cache row count differs: {cache_path}')
        print(f'{code}: reusing Overture {release} water extract', flush=True)
        return cache_path, dict(cached, cache_hit=True, source_mode='overture_download',
                                manifest=str(manifest_path))

    try:
        from overturemaps import record_batch_reader
        from overturemaps.writers import get_writer, copy
    except ImportError as exc:
        raise ImportError('Automatic retrieval requires overturemaps==1.0.2 and pyarrow. '
                          'Install these in this Python environment, or use --water-file.') from exc
    client_version = importlib.metadata.version('overturemaps')
    print(f'{code}: retrieving Overture {release} water for automatic bbox {query_bbox}', flush=True)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f'{code}_download_', dir=cache_dir) as temporary:
        staged = Path(temporary) / cache_path.name
        try:
            reader = record_batch_reader('water', bbox=tuple(query_bbox), release=release,
                                         connect_timeout=30, request_timeout=120, stac=True)
            if reader is None:
                raise RuntimeError('No readable water data returned; this is not evidence of land.')
            with get_writer('geoparquet', str(staged), schema=reader.schema) as writer:
                copy(reader, writer)
            rows = pq.ParquetFile(staged).metadata.num_rows
            if rows == 0:
                raise RuntimeError('The extract contains no water features; inspect the query/source.')
        except Exception as exc:
            raise RuntimeError(
                f'{code}: Overture water retrieval failed: {exc}. Check access to '
                'stac.overturemaps.org and the Overture AWS data host. '
                'No empty-water result was accepted. You can also supply --water-file.'
            ) from exc
        metadata = dict(
            schema_version=1, request=request, release=release, bbox_wgs84=query_bbox,
            downloaded_at_utc=datetime.now(timezone.utc).isoformat(),
            client='overturemaps', client_version=client_version,
            catalog='https://stac.overturemaps.org/catalog.json',
            source=f's3://overturemaps-us-west-2/release/{release}/theme=base/type=water/',
            selection='Feature bounding boxes intersect query bbox; exact strip clipping follows locally',
            sha256=file_hash(staged), size_bytes=staged.stat().st_size, rows=rows,
            download_seconds=time.perf_counter() - started,
            spatial_completeness='Query extent recorded; OSM mapping completeness is not certified')
        staged_manifest = Path(temporary) / manifest_path.name
        staged_manifest.write_text(json.dumps(metadata, indent=2) + '\n')
        staged.replace(cache_path)
        staged_manifest.replace(manifest_path)
    return cache_path, dict(metadata, cache_hit=False, source_mode='overture_download',
                            manifest=str(manifest_path))


def bbox_lonlat(geometry):
    bounds = Transformer.from_crs(TARGET_CRS, 4326, always_xy=True).transform_bounds(
        *geometry.bounds, densify_pts=41)
    if not np.isfinite(bounds).all() or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise ValueError('Cannot construct a finite longitude/latitude query rectangle')
    return tuple(float(v) for v in bounds)


def missing(value):
    return value is None or value is pd.NA or (isinstance(value, (float, np.floating)) and math.isnan(value))


def source_tags(value):
    """Normalise optional OSM tags preserved as dictionaries or Arrow map pairs."""
    if missing(value):
        return {}
    try:
        if isinstance(value, dict):
            tags = value
        else:
            tags = dict((item['key'], item['value']) if isinstance(item, dict) else item
                        for item in value)
        return {str(k).strip().lower(): str(v).strip().lower() for k, v in tags.items()}
    except (KeyError, ValueError, TypeError):
        return {}


def raw_wetland(value):
    return source_tags(value).get('natural') == 'wetland'


def raw_excluded_facility(value):
    tags = source_tags(value)
    return any(tags.get(key) in values for key, values in EXCLUDED_SOURCE_TAGS.items())


def read_water(path, strip, include_intermittent=False):
    frame = gpd.read_parquet(path)
    if frame.crs is None:
        raise ValueError(f'Water GeoParquet has no CRS: {path}')
    if 'subtype' not in frame.columns:
        raise ValueError(f'Expected Overture water subtype column: {path}')
    for column, expected in (('theme', 'base'), ('type', 'water')):
        if column in frame and not frame[column].eq(expected).fillna(False).all():
            raise ValueError(f'Expected Overture {column}={expected} for every source row: {path}')
    optional_fields_absent = [k for k in ('class', 'is_intermittent', 'source_tags') if k not in frame]
    for column in optional_fields_absent:
        frame[column] = None
    count = len(frame)
    # Validate the optional boolean field explicitly: integers/strings are not booleans.
    if any(not missing(v) and not isinstance(v, (bool, np.bool_)) for v in frame['is_intermittent']):
        raise ValueError('is_intermittent must contain booleans or nulls')
    polygon_rows = frame.geometry.geom_type.isin(('Polygon', 'MultiPolygon'))
    nonempty = frame.geometry.notna() & ~frame.geometry.is_empty
    subtype_rows = frame['subtype'].isin(WATER_SUBTYPES)
    class_rows = frame['class'].isna() | frame['class'].isin(SURFACE_CLASSES)
    excluded_facility_rows = (frame['subtype'].isin(EXCLUDED_FACILITY_CODES)
                             | frame['class'].isin(EXCLUDED_FACILITY_CODES)
                             | frame['source_tags'].map(raw_excluded_facility).astype(bool))
    wetland_rows = frame['source_tags'].map(raw_wetland).astype(bool)
    intermittent_rows = frame['is_intermittent'].map(lambda v: not missing(v) and bool(v)).astype(bool)
    # Reasons are disjoint, applied in this order, and sum with admitted rows to total.
    candidates = pd.Series(True, index=frame.index)
    reasons = {}
    for reason, rejected in (
            ('missing_empty_or_nonpolygon', ~(polygon_rows & nonempty)),
            ('excluded_facility', excluded_facility_rows),
            ('unsupported_or_physical_subtype', ~subtype_rows),
            ('unsupported_non_surface_class', ~class_rows),
            ('explicit_wetland_tag', wetland_rows),
            ('explicit_intermittent', intermittent_rows & (not include_intermittent))):
        reasons[reason] = int((candidates & rejected).sum())
        candidates &= ~rejected
    # Keep only fields used below rather than copying all nested source metadata.
    attributes = frame.loc[candidates, [frame.geometry.name, 'subtype', 'class',
                                       'is_intermittent']].copy()
    admitted_count = len(attributes)
    # Bbox filter before projection and topology repair reduces work on large extracts.
    local_bounds = Transformer.from_crs(TARGET_CRS, frame.crs, always_xy=True).transform_bounds(
        *strip.bounds, densify_pts=41)
    positions = attributes.sindex.query(box(*local_bounds))
    attributes = attributes.iloc[positions].copy()
    repaired = 0
    prepared = []
    for i, geometry in attributes.geometry.items():
        try:
            geom, changed = repair_polygon(geometry)
        except ValueError as exc:
            raise ValueError(f'{path}: source feature {i}: {exc}') from exc
        prepared.append(geom)
        repaired += int(changed)
    attributes = attributes.set_geometry(prepared).to_crs(TARGET_CRS)
    selected, ocean_display, unknown_permanence, intermittent_selected = [], [], [], []
    selected_by_subtype, selected_by_class = {}, {}
    selected_features = unknown_count = unknown_class_count = 0
    for geometry, subtype_value, class_value, flag in zip(
            attributes.geometry, attributes['subtype'], attributes['class'],
            attributes['is_intermittent']):
        geom, changed = repair_polygon(geometry)
        repaired += int(changed)
        clipped = polygons(geom.intersection(strip))
        if clipped.is_empty:
            continue
        selected_features += 1
        subtype = str(subtype_value)
        selected_by_subtype[subtype] = selected_by_subtype.get(subtype, 0) + 1
        class_name = '(unspecified)' if missing(class_value) else str(class_value)
        selected_by_class[class_name] = selected_by_class.get(class_name, 0) + 1
        selected.append(clipped)
        # Source labels control only figure colours, never domain membership or treatment.
        if subtype == 'ocean' or class_name in ('ocean', 'sea'):
            ocean_display.append(clipped)
        unknown_class_count += int(missing(class_value))
        if missing(flag):
            unknown_permanence.append(clipped)
            unknown_count += 1
        elif flag:
            intermittent_selected.append(clipped)
    water = polygons(unary_union(selected))
    ocean = polygons(unary_union(ocean_display))
    display_water = {'ocean': ocean, 'other': polygons(water.difference(ocean))}
    unknown = polygons(unary_union(unknown_permanence))
    explicit_intermittent = polygons(unary_union(intermittent_selected))
    audit = dict(source_feature_count=count,
                 admitted_surface_feature_count=admitted_count,
                 rejected_feature_counts=reasons,
                 unsupported_subtypes={str(k): int(v) for k, v in frame.loc[~subtype_rows, 'subtype'].value_counts(dropna=False).items()},
                 unsupported_classes={str(k): int(v) for k, v in frame.loc[~class_rows, 'class'].value_counts(dropna=False).items()},
                 optional_fields_absent=optional_fields_absent,
                 bounding_box_candidate_count=len(attributes),
                 selected_intersecting_feature_count=selected_features,
                 selected_by_subtype=selected_by_subtype,
                 selected_by_class=selected_by_class,
                 selected_unknown_permanence_feature_count=unknown_count,
                 selected_unspecified_class_feature_count=unknown_class_count,
                 geometry_repairs=repaired,
                 overlap_policy='All accepted water polygons dissolved into one union; overlaps counted once',
                 unknown_permanence_water_area_km2=unknown.area/1e6,
                 unknown_permanence_area_note=('Union of areas supported by any selected null flag; '
                                              'may overlap areas also supported by explicit flags'),
                 explicitly_intermittent_selected_area_km2=explicit_intermittent.area/1e6,
                 permanence_note='Null or absent is_intermittent means unknown, not proven permanent')
    return water, audit, display_water


def write_layer(path, name, geometry):
    parts = polygon_parts(geometry)
    # Store a single dissolved multipart geometry; holes remain polygon interiors.
    rows = [] if not parts else [MultiPolygon(parts)]
    frame = gpd.GeoDataFrame({'area_km2': [g.area/1e6 for g in rows]},
                             geometry=rows, crs=TARGET_CRS)
    frame.to_file(path, layer=name, driver='GPKG', engine='pyogrio',
                  geometry_type='MultiPolygon', index=False)


def overview_plot(path_png, path_pdf, code, boundary, strip, water, display_water, buffer_km):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch, Rectangle

    def detail_bounds(geometry, fallback):
        parts = polygon_parts(geometry)
        point = (max(parts, key=lambda p: p.area) if parts else fallback).representative_point()
        extent = max(strip.bounds[2]-strip.bounds[0], strip.bounds[3]-strip.bounds[1])
        radius = min(extent*.35, max(12000.0, min(25000.0, math.sqrt(strip.area)/8)))
        return point.x-radius, point.y-radius, point.x+radius, point.y+radius

    bounds = strip.bounds
    pad = max(bounds[2]-bounds[0], bounds[3]-bounds[1]) * .035
    full = bounds[0]-pad, bounds[1]-pad, bounds[2]+pad, bounds[3]+pad
    panels = [(f'Complete {buffer_km:g} km outer strip', full),
              ('1: Water detail', detail_bounds(water, strip)),
              ('2: Border-water detail', detail_bounds(display_water['other'], strip))]
    fig, axes = plt.subplots(1, 3, figsize=(16, 7), constrained_layout=True)
    for ax, (title, panel_bounds) in zip(axes, panels):
        clip = box(*panel_bounds)
        for geometry, color, edge in ((boundary, '#e8e5df', '#686761'),
                                      (strip, '#f8e9ce', 'none'),
                                      (water, '#4b9dce', 'none'),
                                      (display_water['other'], '#159a99', 'none')):
            visible = polygons(geometry.intersection(clip))
            if not visible.is_empty:
                gpd.GeoSeries([visible], crs=TARGET_CRS).plot(ax=ax, color=color,
                                                           edgecolor=edge, linewidth=.35)
        ax.set_xlim(panel_bounds[0], panel_bounds[2])
        ax.set_ylim(panel_bounds[1], panel_bounds[3])
        ax.set_aspect('equal')
        ax.set_title(title, fontsize=11)
        ax.set_axis_off()
        width = panel_bounds[2]-panel_bounds[0]
        scale = 10 ** math.floor(math.log10(width/5))
        scale = max([n*scale for n in (1, 2, 5) if n*scale <= width/4] or [scale])
        x, y = panel_bounds[0]+width*.07, panel_bounds[1]+(panel_bounds[3]-panel_bounds[1])*.06
        ax.plot([x, x+scale], [y, y], color='#333333', linewidth=2)
        ax.text(x+scale/2, y+(panel_bounds[3]-panel_bounds[1])*.015,
                f'{scale/1000:g} km', ha='center', fontsize=8)
    for number, (_, detail) in enumerate(panels[1:], 1):
        x0, y0, x1, y1 = detail
        axes[0].add_patch(Rectangle((x0, y0), x1-x0, y1-y0, facecolor='none',
                                   edgecolor='#343434', linewidth=.9))
        axes[0].text(x0, y1, str(number), fontsize=9, va='bottom', fontweight='bold',
                     bbox=dict(facecolor='white', edgecolor='none', alpha=.85, pad=1))
    fig.suptitle(f'{code}: mapped water outside the country boundary', fontsize=17)
    fig.legend(handles=[Patch(facecolor='#e8e5df', label='Original country'),
                        Patch(facecolor='#4b9dce', label='Water buffer'),
                        Patch(facecolor='#159a99', label='Other mapped water (same treatment)'),
                        Patch(facecolor='#f8e9ce', label='Not selected as water (not proven land)')],
               loc='lower center', ncol=2, fontsize=9, bbox_to_anchor=(.5, -.015))
    fig.text(.5, -.04, 'EPSG:3035 | Blue and teal form one water buffer; source labels affect display only\n'
             'Source: Overture Maps / © OpenStreetMap contributors',
             ha='center', fontsize=8, color='#555555')
    fig.savefig(path_png, dpi=300, bbox_inches='tight')
    fig.savefig(path_pdf, bbox_inches='tight')
    plt.close(fig)


def prepare_country(args, code):
    started = time.perf_counter()
    boundary_path = args.boundary_dir / f'{code}.shp'
    reference_path = args.reference_dir / f'{code}_reference_grid.json'
    boundary, repairs = read_boundary(boundary_path)
    reference = load_reference(reference_path, code)
    strip = polygons(boundary.buffer(args.buffer_km*1000, quad_segs=32).difference(boundary))
    if strip.is_empty:
        raise ValueError(f'{code}: buffer operation produced no polygon area')
    bbox = bbox_lonlat(strip)
    if args.water_file:
        source = args.water_file.expanduser().resolve()
        provenance = dict(mode='local_file', release=None,
                          query_bbox_epsg4326=None,
                          extract_completeness='Unverified; caller supplied the local source')
    else:
        source, provenance = download_source(args, code, bbox)
    print(f'{code}: identifying mapped water in the {args.buffer_km:g} km outer strip', flush=True)
    water, audit, display_water = read_water(source, strip, args.include_intermittent)
    not_selected = polygons(strip.difference(water))
    water_outside_strip = water.difference(strip).area
    tolerance = max(.01, strip.area*1e-10)
    if (water_outside_strip > tolerance or water.intersection(boundary).area > tolerance
            or abs(strip.area-water.area-not_selected.area) > tolerance):
        raise ValueError(f'{code}: geometry area-conservation check failed')
    outdir = args.output_root / code / 'domain'
    outdir.mkdir(parents=True, exist_ok=True)
    names = dict(polygons=f'{code}_water_buffer.gpkg', report=f'{code}_water_buffer.json',
                 overview_png=f'{code}_water_buffer_overview.png',
                 overview_pdf=f'{code}_water_buffer_overview.pdf')
    report = dict(schema_version=2, country=code, buffer_km=args.buffer_km,
                  created_utc=datetime.now(timezone.utc).isoformat(),
                  status='mapped_water_domain_prepared' if not water.is_empty else 'no_mapped_water_selected',
                  domain_rule='(buffer(country, distance) minus country) intersect selected mapped water polygons',
                  crs='EPSG:3035', buffer_quad_segs=32,
                  method=dict(source_model='Overture Maps water polygons',
                              water_category='One combined water buffer; all selected water receives the same treatment',
                              selected_subtypes=sorted(WATER_SUBTYPES),
                              selected_classes=sorted(SURFACE_CLASSES),
                              unspecified_class='Included when polygon and subtype are accepted',
                              excluded_facility_codes=sorted(EXCLUDED_FACILITY_CODES),
                              excluded_facility_rule='Reject a feature when its class OR subtype matches an excluded code, or a raw source tag matches',
                              excluded_source_tags={k: sorted(v) for k, v in EXCLUDED_SOURCE_TAGS.items()},
                              facility_exclusion_scope=('Feature-level exclusion; a rejected feature cannot add area. '
                                                        'Independently accepted overlapping water polygons remain eligible'),
                              display_only=('Blue: source subtype ocean or class ocean/sea; teal: remaining selected water. '
                                            'Both colours belong to the same model domain and treatment; '
                                            'these labels do not establish freshwater or saltwater conditions'),
                              include_intermittent=args.include_intermittent,
                              unknown_permanence='Included and reported separately',
                              islands='Interior polygon holes preserved',
                              coastline_definition=('OSM mean high-water springs; intertidal areas '
                                                    'can lie on the water side of the coastline'),
                              absent_water=CAVEAT,
                              rasterisation='Not performed; boundary-straddling cell rule remains a later step'),
                  area_km2=dict(country=boundary.area/1e6, outer_strip=strip.area/1e6,
                                water_buffer=water.area/1e6, not_selected_as_water=not_selected.area/1e6),
                  water_share_of_outer_strip_percent=100*water.area/strip.area,
                  grid=expanded_grid(reference, strip.bounds),
                  source_audit=audit, download=provenance,
                  required_query_bbox_epsg4326=list(bbox),
                  inputs=dict(water=file_metadata(source), reference=file_metadata(reference_path),
                              boundary=[file_metadata(boundary_path.with_suffix(s))
                                        for s in ('.shp', '.shx', '.dbf', '.prj', '.cpg')
                                        if boundary_path.with_suffix(s).is_file()],
                              boundary_geometry_repairs=repairs),
                  outputs={k: str((outdir/name).resolve()) for k, name in names.items()})
    with tempfile.TemporaryDirectory(prefix=f'{code}_', dir=outdir) as tmp:
        tmp = Path(tmp)
        gpkg = tmp / names['polygons']
        for name, geometry in (('country_boundary', boundary), ('outer_strip', strip),
                               ('water_buffer', water), ('not_selected_as_water', not_selected)):
            write_layer(gpkg, name, geometry)
        overview_plot(tmp/names['overview_png'], tmp/names['overview_pdf'], code,
                      boundary, strip, water, display_water, args.buffer_km)
        report['processing_seconds'] = time.perf_counter()-started
        (tmp/names['report']).write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        for key in ('polygons', 'overview_png', 'overview_pdf', 'report'):
            (tmp/names[key]).replace(outdir/names[key])
    print(f'{code}: mapped water {water.area/1e6:,.3f} km²; '
          f'not selected {not_selected.area/1e6:,.3f} km²; {outdir/names["report"]}', flush=True)
    return report


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.countries = list(dict.fromkeys(args.countries))
    if args.water_file and len(args.countries) != 1:
        parser.error('--water-file requires exactly one country per run')
    args.pipeline_dir = args.pipeline_dir.expanduser().resolve()
    args.boundary_dir = args.boundary_dir or args.pipeline_dir/'intermediate'/'boundaries'
    args.reference_dir = args.reference_dir or args.pipeline_dir/'intermediate'/'reference_grid'
    args.output_root = args.output_root or args.pipeline_dir/'intermediate'/'water_buffer'/f'{args.buffer_km:g}km'
    args.boundary_dir = args.boundary_dir.expanduser().resolve()
    args.reference_dir = args.reference_dir.expanduser().resolve()
    args.output_root = args.output_root.expanduser().resolve()
    # Check every requested country before starting any network download or output.
    required = [args.boundary_dir/f'{code}{suffix}' for code in args.countries
                for suffix in ('.shp', '.shx', '.dbf', '.prj')]
    required.extend(args.reference_dir/f'{code}_reference_grid.json' for code in args.countries)
    if args.water_file:
        required.append(args.water_file.expanduser().resolve())
    missing_paths = [str(path) for path in required if not path.is_file()]
    if missing_paths:
        parser.error('Required input files are missing:\n  ' + '\n  '.join(missing_paths))
    for code in args.countries:
        prepare_country(args, code)


if __name__ == '__main__':
    main()
