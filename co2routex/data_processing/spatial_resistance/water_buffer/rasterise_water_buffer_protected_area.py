#!/usr/bin/env python3
"""Area-weight protected-area resistance inside the optional water buffer.

Install beside prepare_water_buffer.py. Python >=3.10. No bathymetry needed.
Dependencies: numpy, pandas, geopandas, pyogrio, shapely>=2.1, pyproj,
rasterio and affine. Original country inputs and outputs remain untouched.

Within each eligible 100 m cell, average the pointwise maximum (1, 10, 30)
over its exact intersection with the unified vector water-buffer geometry.
IUCN II has factor 30; every other protected polygon has factor 10. No realm,
country or designation-status attribute is used to exclude source features.
Factor reference: https://doi.org/10.1080/24725854.2025.2602823
"""
import argparse
from collections import Counter
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
import pandas as pd
from pyproj import CRS, Transformer
import pyogrio
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window, transform as window_transform
import shapely
from shapely.affinity import translate

SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SCRIPT_DIR.parents[2] / 'database' / 'pipeline'
COUNTRIES = ('NL', 'DE', 'NO')
TARGET_CRS = CRS.from_epsg(3035)
PART_FOLDERS = tuple(f'WDPA_Jun2026_Public_shp_{i}' for i in range(3))
ELIGIBILITY = 'Strict 100 m cell centre lies inside combined vector water; exact polygon-boundary centres excluded'
WATER_CATEGORY = 'One combined water buffer; all selected water receives the same treatment'
CACHE_VERSION = 1
NODATA = -9999.0
BACKGROUND = 1.0
GEOMETRY_BATCH = 8192
QUERY_CELLS = 512
AREA_TOLERANCE_M2 = 1e-6
# Recovery limits for already-clipped overlay residue, not a physical buffer.
ROUNDOFF_ULPS = 512
ROUNDOFF_DISTANCE_CAP_M = 1e-6
ROUNDOFF_AREA_CAP_M2 = 1e-4
ROUNDOFF_RELATIVE_AREA_CAP = 1e-8
AREA_IMPLEMENTATION = 'exact_boundary_halo_and_independent_cell_rebuild_v3'
OPTIONAL_FIELDS = ('REALM', 'MARINE', 'ISO3', 'PRNT_ISO3', 'PARENT_ISO3',
                   'STATUS', 'SITE_TYPE', 'SITE_ID', 'SITE_PID', 'WDPAID', 'WDPA_PID')


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
    parser.add_argument('--buffer-root', type=Path, help='scenario root containing COUNTRY/domain and COUNTRY/raster')
    parser.add_argument('--output-root', type=Path, help='scenario root for COUNTRY/raster outputs')
    parser.add_argument('--buffer-km', type=positive_float, default=5.0)
    parser.add_argument('--source-files', nargs='+', type=Path,
                        help='raw polygon SHP or GPKG files; relative paths are relative to raw/protected_areas')
    parser.add_argument('--source-layer', help='GPKG layer name; required when a source contains multiple layers')
    parser.add_argument('--block-size', type=block_size, default=128)
    parser.add_argument('--check-block', nargs=2, type=int, metavar=('ROW', 'COL'),
                        help='check one production block at these row/column offsets; requires one country; '
                             'writes diagnostics only, with normal source and domain validation')
    parser.add_argument('--rebuild-vectors', action='store_true', help='ignore an otherwise valid clipped-vector cache')
    return parser


def resolve_paths(args):
    args.countries = list(dict.fromkeys(args.countries))
    if getattr(args, 'check_block', None) is not None:
        if len(args.countries) != 1:
            raise ValueError('--check-block requires exactly one country')
        if any(value < 0 or value % args.block_size for value in args.check_block):
            raise ValueError('--check-block offsets must be nonnegative multiples of --block-size')
    args.pipeline_dir = args.pipeline_dir.expanduser().resolve()
    scenario = args.pipeline_dir / 'intermediate' / 'water_buffer' / f'{args.buffer_km:g}km'
    args.buffer_root = (args.buffer_root or scenario).expanduser().resolve()
    args.output_root = (args.output_root or args.buffer_root).expanduser().resolve()
    return args


def input_paths(args, code):
    directory = args.buffer_root / code
    return dict(gpkg=directory / 'domain' / f'{code}_water_buffer.gpkg',
                domain_report=directory / 'domain' / f'{code}_water_buffer.json',
                raster_report=directory / 'raster' / f'{code}_water_buffer_raster.json',
                mask=directory / 'raster' / f'{code}_water_buffer_mask_100m.tif')


def file_state(path):
    stat = Path(path).stat()
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)


def file_metadata(path):
    path = Path(path)
    before = file_state(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    if file_state(path) != before:
        raise RuntimeError(f'{path}: input changed while hashing')
    return dict(path=str(path.resolve()), size_bytes=before[0], mtime_ns=before[1], sha256=digest.hexdigest())


def _content_key(metadata):
    return dict(size_bytes=metadata['size_bytes'], sha256=metadata['sha256'])


def validate_grid(grid, label):
    if CRS.from_user_input(grid.get('crs')) != TARGET_CRS:
        raise ValueError(f'{label}: expected EPSG:3035')
    for name in ('width', 'height', 'fine_width', 'fine_height'):
        value = grid.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f'{label}/{name}: expected a positive integer')
    transform = np.asarray(grid.get('transform'), dtype=float)
    fine = np.asarray(grid.get('fine_transform'), dtype=float)
    if (transform.shape != (6,) or not np.isfinite(transform).all()
            or not np.allclose(transform[[0, 1, 3, 4]], [100, 0, 0, -100], rtol=0, atol=1e-8)):
        raise ValueError(f'{label}: expected finite north-up 100 m transform')
    expected = transform.copy()
    expected[[0, 1, 3, 4]] /= 10
    if (grid.get('factor') != 10 or fine.shape != (6,) or not np.isfinite(fine).all()
            or not np.allclose(fine, expected, rtol=0, atol=1e-8)
            or grid['fine_width'] != 10 * grid['width'] or grid['fine_height'] != 10 * grid['height']):
        raise ValueError(f'{label}: fine grid must nest exactly in 100 m cells')
    return Affine(*transform)


def windows(width, height, size):
    for row in range(0, height, size):
        for column in range(0, width, size):
            yield Window(column, row, min(size, width-column), min(size, height-row))


def validate_mask_metadata(source, grid, transform, code):
    if (source.count != 1 or source.dtypes != ('uint8',) or source.nodata != 255
            or source.width != grid['width'] or source.height != grid['height']
            or source.crs is None or CRS.from_user_input(source.crs) != TARGET_CRS
            or not np.allclose(list(source.transform)[:6], list(transform)[:6], rtol=0, atol=1e-8)):
        raise ValueError(f'{code}: mask CRS, grid, dtype or NoData differs from domain report')


def _strict_centres(water, transform, window):
    x0, y0 = transform * (window.col_off, window.row_off)
    height, width = int(window.height), int(window.width)
    footprint = shapely.box(x0, y0-height*100, x0+width*100, y0)
    if water.is_empty or not shapely.intersects(water, footprint):
        return np.zeros((height, width), dtype=bool)
    x = x0 + (np.arange(width)+0.5)*100
    y = y0 - (np.arange(height)+0.5)*100
    return shapely.contains_xy(water, x[None, :], y[:, None])


def _checked_mask(data, water, transform, window, code):
    if not np.isin(data, [0, 1]).all():
        raise ValueError(f'{code}: completed domain mask must contain only 0 and 1')
    included = data == 1
    expected = _strict_centres(water, transform, window)
    if not np.array_equal(included, expected):
        raise ValueError(f'{code}: mask differs from strict vector-water cell centres; regenerate the domain stage')
    return included


def _area_matches(value, measured, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or value < 0 or abs(value*1e6-measured) > max(0.1, measured*1e-10)):
        raise ValueError(f'{label}: vector water area differs from report')


def load_country_inputs(args, code):
    paths = input_paths(args, code)
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    states = {name: file_state(path) for name, path in paths.items()}
    inputs = {name: file_metadata(path) for name, path in paths.items()}
    report = json.loads(paths['raster_report'].read_text())
    vector_report = json.loads(paths['domain_report'].read_text())
    for item, version, label in ((report, 1, 'raster'), (vector_report, 2, 'vector')):
        if item.get('schema_version') != version or item.get('country') != code:
            raise ValueError(f'{code}: expected schema {version} {label} report for this country')
        distance = item.get('buffer_km')
        if (isinstance(distance, bool) or not isinstance(distance, (int, float))
                or not math.isclose(distance, args.buffer_km, abs_tol=1e-10, rel_tol=0)):
            raise ValueError(f'{code}: buffer distance differs from {label} report')
    for name in ('gpkg', 'domain_report'):
        recorded = report.get('inputs', {}).get(name, {})
        if recorded.get('sha256') != inputs[name]['sha256']:
            raise ValueError(f'{code}: {name} content hash differs from raster-domain report; regenerate the domain stage')
    grid = report['grid']
    transform = validate_grid(grid, f'{code}/raster grid')
    other_transform = validate_grid(vector_report['grid'], f'{code}/vector grid')
    if (grid['width'] != vector_report['grid']['width'] or grid['height'] != vector_report['grid']['height']
            or not np.allclose(list(transform)[:6], list(other_transform)[:6], atol=1e-8, rtol=0)):
        raise ValueError(f'{code}: vector and raster domain grids differ')
    if CRS.from_user_input(vector_report.get('crs')) != TARGET_CRS:
        raise ValueError(f'{code}: vector report must specify EPSG:3035')
    method = vector_report.get('method', {})
    if method.get('water_category') != WATER_CATEGORY:
        raise ValueError(f'{code}: expected unified vector-water policy')
    if not {'swimming_pool', 'wastewater', 'sewage'}.issubset(set(method.get('excluded_facility_codes', []))):
        raise ValueError(f'{code}: vector report lacks agreed facility exclusions')
    rules = report.get('rules', {})
    if rules.get('eligibility') != ELIGIBILITY or rules.get('excluded_mask') != 0 or rules.get('mask_nodata') != 255:
        raise ValueError(f'{code}: incompatible domain mask rules')
    eligible = report.get('diagnostics', {}).get('eligible_cells')
    if isinstance(eligible, bool) or not isinstance(eligible, int) or not 0 <= eligible <= grid['width']*grid['height']:
        raise ValueError(f'{code}: invalid eligible cell count')
    allowed = {'water_buffer_raster_domain_prepared', 'no_eligible_cell_centres', 'no_mapped_water_selected'}
    if report.get('status') not in allowed or ((report['status'] == 'water_buffer_raster_domain_prepared') != (eligible > 0)):
        raise ValueError(f'{code}: raster report status contradicts eligible cell count')
    frame = pyogrio.read_dataframe(paths['gpkg'], layer='water_buffer')
    if frame.crs is None or CRS.from_user_input(frame.crs) != TARGET_CRS:
        raise ValueError(f'{code}: water layer must use EPSG:3035')
    geometries = []
    for row, geometry in enumerate(frame.geometry):
        if geometry is None:
            raise ValueError(f'{code}: missing water geometry at row {row}')
        if geometry.is_empty:
            continue
        if (geometry.geom_type not in ('Polygon', 'MultiPolygon') or not geometry.is_valid
                or not np.isfinite(shapely.get_coordinates(geometry)).all()):
            raise ValueError(f'{code}: invalid vector water geometry at row {row}')
        geometries.append(geometry)
    water = shapely.union_all(geometries) if geometries else shapely.GeometryCollection()
    if not water.is_valid:
        raise ValueError(f'{code}: invalid water union')
    expected_status = 'no_mapped_water_selected' if water.is_empty else 'mapped_water_domain_prepared'
    if vector_report.get('status') != expected_status:
        raise ValueError(f'{code}: vector report status contradicts water geometry')
    _area_matches(vector_report.get('area_km2', {}).get('water_buffer'), water.area, f'{code}/vector')
    _area_matches(report.get('area_km2', {}).get('exact_vector_water'), water.area, f'{code}/raster')
    extent = shapely.box(transform.c, transform.f-grid['height']*100,
                         transform.c+grid['width']*100, transform.f)
    if water.difference(extent).area > max(0.1, water.area*1e-10):
        raise ValueError(f'{code}: vector water falls outside expanded grid')
    shapely.prepare(water)
    with rasterio.open(paths['mask']) as source:
        validate_mask_metadata(source, grid, transform, code)
    if any(file_state(path) != states[name] for name, path in paths.items()):
        raise RuntimeError(f'{code}: inputs changed during preflight')
    return dict(paths=paths, inputs=inputs, states=states, report=report, vector_report=vector_report,
                grid=grid, transform=transform, water=water, expected_eligible_cells=eligible)


def source_components(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() == '.gpkg':
        return [path]
    if path.suffix.lower() != '.shp':
        raise ValueError(f'{path}: expected SHP or GPKG polygon data')
    supported = {'.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix', '.sbn', '.sbx'}
    components = sorted(p for p in path.parent.iterdir()
                        if p.is_file() and p.stem.lower() == path.stem.lower() and p.suffix.lower() in supported)
    extensions = [p.suffix.lower() for p in components]
    if not {'.shp', '.shx', '.dbf', '.prj'}.issubset(extensions) or len(extensions) != len(set(extensions)):
        raise ValueError(f'{path}: missing or ambiguous SHP/SHX/DBF/PRJ companions')
    return components


def inspect_sources(args):
    raw = args.pipeline_dir / 'raw' / 'protected_areas'
    if args.source_files:
        paths = [(p if p.is_absolute() else raw/p).expanduser().resolve() for p in args.source_files]
    else:
        paths = []
        for name in PART_FOLDERS:
            folder = raw/name
            matches = sorted(p for p in folder.rglob('*') if p.suffix.lower() == '.shp' and 'polygon' in p.stem.lower())
            if len(matches) != 1:
                raise ValueError(f'Expected exactly one polygon shapefile in {folder}; found {len(matches)}. Use --source-files if needed.')
            paths.append(matches[0].resolve())
    if not paths or len(paths) != len(set(paths)):
        raise ValueError('Expected distinct raw polygon source files')
    sources = []
    for path in paths:
        components = source_components(path)
        states = {str(p): file_state(p) for p in components}
        print(f'Checking raw protected-area source and SHA-256: {path.name}', flush=True)
        metadata = [dict(component=p.suffix.lower(), **file_metadata(p)) for p in components]
        layer = None
        if path.suffix.lower() == '.gpkg':
            layers = pyogrio.list_layers(path)
            names = [str(row[0]) for row in layers]
            if args.source_layer:
                if args.source_layer not in names:
                    raise ValueError(f'{path}: requested source layer not present')
                layer = args.source_layer
            elif len(names) != 1:
                raise ValueError(f'{path}: multiple layers; specify --source-layer')
            else:
                layer = names[0]
        info = pyogrio.read_info(path, layer=layer, force_feature_count=True)
        fields = {str(field).upper(): str(field) for field in info['fields']}
        if not info['crs'] or 'IUCN_CAT' not in fields:
            raise ValueError(f'{path}: required CRS or IUCN_CAT field missing')
        geometry_type = str(info['geometry_type']).replace(' Z', '').replace(' M', '').replace('25D ', '')
        if geometry_type not in ('Polygon', 'MultiPolygon'):
            raise ValueError(f'{path}: source must be a polygon layer, found {info["geometry_type"]}')
        if int(info['features']) <= 0:
            raise ValueError(f'{path}: raw source is empty or unreadable')
        sources.append(dict(path=path, layer=layer, crs=CRS.from_user_input(info['crs']), fields=fields,
                            feature_count=int(info['features']), metadata=metadata, states=states,
                            geometry_type=str(info['geometry_type'])))
    assert_sources_unchanged(sources)
    return sources


def assert_sources_unchanged(sources):
    for source in sources:
        if set(str(p) for p in source_components(source['path'])) != set(source['states']):
            raise RuntimeError(f'{source["path"]}: source companions changed during run')
        for path, state in source['states'].items():
            if file_state(path) != state:
                raise RuntimeError(f'{path}: source changed during run; retry with stable inputs')


def _polygon_parts(geometry):
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry]
    if geometry.geom_type in ('MultiPolygon', 'GeometryCollection'):
        return [part for child in geometry.geoms for part in _polygon_parts(child)]
    return []


def _valid_polygon(geometry, context):
    if geometry is None or geometry.is_empty or geometry.geom_type not in ('Polygon', 'MultiPolygon'):
        raise ValueError(f'{context}: expected nonempty Polygon/MultiPolygon geometry')
    if not np.isfinite(shapely.get_coordinates(geometry)).all():
        raise ValueError(f'{context}: non-finite polygon coordinates')
    repaired = int(not geometry.is_valid)
    if repaired:
        parts = _polygon_parts(shapely.make_valid(geometry))
        geometry = shapely.union_all(parts) if parts else shapely.GeometryCollection()
    if geometry.is_empty or geometry.area <= 0 or not geometry.is_valid:
        raise ValueError(f'{context}: no valid polygon area after repair')
    return geometry, repaired


def category(value):
    return '<missing>' if pd.isna(value) else str(value).strip().upper()


def _attribute_text(value):
    return '<missing>' if pd.isna(value) else str(value)


def _empty_frame():
    return gpd.GeoDataFrame(dict(multiplier=pd.Series(dtype='uint8'), iucn_category=pd.Series(dtype='str'),
                                source_index=pd.Series(dtype='int64'), source_fid=pd.Series(dtype='str')),
                            geometry=gpd.GeoSeries([], crs='EPSG:3035'), crs='EPSG:3035')


def _cache_key(prepared, sources):
    return dict(version=CACHE_VERSION, water_domain=_content_key(prepared['inputs']['gpkg']),
                sources=[dict(layer=s['layer'], components=[dict(component=m['component'], **_content_key(m))
                                                           for m in s['metadata']]) for s in sources],
                crs='EPSG:3035', category_rule='strip/uppercase II=30; every other category including missing=10',
                selection='spatial bbox then exact unified water clip; no realm, ISO or status exclusion')


def prepare_vectors(args, code, prepared, sources, stage):
    outdir = args.output_root/code/'raster'
    target = outdir/f'{code}_water_buffer_protected_area_polygons.gpkg'
    manifest_path = outdir/f'{code}_water_buffer_protected_area_polygons.manifest.json'
    key = _cache_key(prepared, sources)
    water = prepared['water']
    if not args.rebuild_vectors and target.is_file() and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
            current = file_metadata(target)
            if manifest.get('key') == key and manifest.get('output', {}).get('sha256') == current['sha256']:
                frame = pyogrio.read_dataframe(target, layer='protected_areas')
                if (frame.crs is None or CRS.from_user_input(frame.crs) != TARGET_CRS
                        or 'multiplier' not in frame or not frame['multiplier'].isin([10, 30]).all()):
                    raise ValueError('Invalid clipped polygon cache metadata')
                for geometry in frame.geometry:
                    if (geometry is None or geometry.is_empty or not geometry.is_valid
                            or geometry.geom_type not in ('Polygon', 'MultiPolygon')
                            or geometry.difference(water).area > AREA_TOLERANCE_M2):
                        raise ValueError('Invalid cached polygon geometry')
                print(f'{code}: reusing hashed water-buffer protected polygon cache', flush=True)
                return frame, manifest['report'], True, []
        except (OSError, ValueError, KeyError):
            pass  # A damaged or stale cache is rebuilt from the validated raw sources.
    started = time.perf_counter()
    records, source_reports = [], []
    for index, source in enumerate(sources):
        fields = source['fields']
        selected_names = ['IUCN_CAT'] + [name for name in OPTIONAL_FIELDS if name in fields]
        columns = [fields[name] for name in selected_names]
        diagnostic_names = [name for name in ('REALM', 'MARINE', 'ISO3', 'PRNT_ISO3', 'PARENT_ISO3', 'STATUS', 'SITE_TYPE') if name in fields]
        counts = {name: Counter() for name in ['IUCN_CAT']+diagnostic_names}
        candidate_counts = {name: Counter() for name in ['IUCN_CAT']+diagnostic_names}
        report = dict(source_index=index, source=str(source['path']), layer=source['layer'],
                      source_feature_count=source['feature_count'], available_fields=list(fields),
                      bbox_candidates=0, retained_source_features=0, polygon_parts=0,
                      source_geometry_repairs=0, projected_geometry_repairs=0)
        if not water.is_empty:
            transformer = Transformer.from_crs(TARGET_CRS, source['crs'], always_xy=True)
            xmin, ymin, xmax, ymax = water.bounds
            # Densified transformed edges approximate projection extrema. Pad
            # the query by one coarse cell so tiny inward sampling errors cannot
            # drop a feature near the water bounds; exact clipping follows.
            bbox = transformer.transform_bounds(xmin-100, ymin-100, xmax+100, ymax+100, densify_pts=101)
            if not np.isfinite(bbox).all() or bbox[2] < bbox[0] or bbox[3] < bbox[1]:
                raise ValueError(f'{source["path"]}: unusable transformed water bounds')
            epsilon = max(1e-9, max(bbox[2]-bbox[0], bbox[3]-bbox[1])*1e-9)
            bbox = (bbox[0]-epsilon, bbox[1]-epsilon, bbox[2]+epsilon, bbox[3]+epsilon)
            candidates = pyogrio.read_dataframe(source['path'], layer=source['layer'], bbox=bbox,
                                                columns=columns, fid_as_index=True, use_arrow=False)
            report['bbox_candidates'] = len(candidates)
            print(f'{code}: {source["path"].name}: {len(candidates):,} spatial candidates; exact water clip', flush=True)
            repaired = []
            for fid, row in candidates.iterrows():
                for name in candidate_counts:
                    value = category(row[fields[name]]) if name == 'IUCN_CAT' else _attribute_text(row[fields[name]])
                    candidate_counts[name].update([value])
                geometry, repairs = _valid_polygon(row.geometry, f'{source["path"].name}/FID {fid}')
                report['source_geometry_repairs'] += repairs
                repaired.append(geometry)
            if len(candidates):
                candidates = candidates.copy()
                candidates.geometry = repaired
                candidates = candidates.to_crs(TARGET_CRS)
                for fid, row in candidates.iterrows():
                    geometry, repairs = _valid_polygon(row.geometry, f'{source["path"].name}/FID {fid} projected')
                    report['projected_geometry_repairs'] += repairs
                    if not shapely.intersects(water, geometry):
                        continue
                    clipped = shapely.intersection(geometry, water)
                    parts = _polygon_parts(clipped)
                    if not parts:
                        continue
                    normal = category(row[fields['IUCN_CAT']])
                    provenance = {name: _attribute_text(row[fields[name]]) for name in selected_names if name != 'IUCN_CAT'}
                    report['retained_source_features'] += 1
                    for name in counts:
                        counts[name].update([category(row[fields[name]]) if name == 'IUCN_CAT' else _attribute_text(row[fields[name]])])
                    for part in parts:
                        if part.area <= 0:
                            continue
                        records.append(dict(multiplier=30 if normal == 'II' else 10,
                                            iucn_category=normal, source_index=index, source_fid=str(fid),
                                            **provenance, geometry=part))
                        report['polygon_parts'] += 1
        report['candidate_attribute_counts'] = {name: dict(sorted(count.items())) for name, count in candidate_counts.items()}
        report['retained_attribute_counts'] = {name: dict(sorted(count.items())) for name, count in counts.items()}
        source_reports.append(report)
    frame = gpd.GeoDataFrame(records, geometry='geometry', crs='EPSG:3035') if records else _empty_frame()
    frame['multiplier'] = frame['multiplier'].astype('uint8')
    report = dict(country=code, polygon_parts=len(frame), retained_source_features=sum(s['retained_source_features'] for s in source_reports),
                  multiplier_part_counts={str(k): int(v) for k, v in frame['multiplier'].value_counts().items()},
                  sources=source_reports, preparation_seconds=time.perf_counter()-started,
                  selection='All source realms, country attributes and statuses; spatial clipping only',
                  no_intersections_meaning='No mapped polygon intersection in the supplied sources; not proof that protection is absent')
    assert_sources_unchanged(sources)
    staged_gpkg = stage/target.name
    pyogrio.write_dataframe(frame, staged_gpkg, layer='protected_areas', driver='GPKG',
                            geometry_type='MultiPolygon', promote_to_multi=True,
                            layer_options={'SPATIAL_INDEX': 'YES'})
    output = file_metadata(staged_gpkg)
    output['path'] = str(target)
    manifest = dict(schema_version=1, key=key, output=output, report=report)
    staged_manifest = stage/manifest_path.name
    staged_manifest.write_text(json.dumps(manifest, indent=2, allow_nan=False)+'\n')
    return frame, report, False, [staged_gpkg, staged_manifest]


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
    # A halo preserves boundary segments just beyond a processing window.
    # Expand only the candidates for exact intersection, never the geometry.
    height, width = shape
    halo_transform = transform * Affine.translation(-1, -1)
    halo_shape = (height + 2, width + 2)
    covered = rasterize([(geometry, 1)], out_shape=halo_shape, transform=halo_transform,
                        fill=0, dtype='uint8', all_touched=True)[1:-1, 1:-1].astype(bool)
    cell_area = abs(transform.a * transform.e)
    result[covered & included] = cell_area
    edges = rasterize([(geometry.boundary, 1)], out_shape=halo_shape,
                      transform=halo_transform, fill=0, dtype='uint8', all_touched=True).astype(bool)
    boundary_candidates = np.zeros(shape, dtype=bool)
    for row_shift in range(3):
        for col_shift in range(3):
            boundary_candidates |= edges[row_shift:row_shift + height, col_shift:col_shift + width]
    rows, cols = np.nonzero(boundary_candidates & included)
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


def _spill_recovery_limit(child, parent, water_area, tolerance, epsilon):
    """Bound extra numerical residue by area, relative area and proximity.

The distance allowance is an engineering guard based on coordinate spacing,
not a formal GEOS error bound. Buffered geometry is used only for validation.
"""
    limit = max(tolerance, min(tolerance + epsilon * (child.length + parent.length),
                               ROUNDOFF_AREA_CAP_M2, water_area * ROUNDOFF_RELATIVE_AREA_CAP))
    spill = float(shapely.area(shapely.difference(child, parent)))
    acceptable = math.isfinite(spill) and spill <= limit
    if acceptable and spill > tolerance:
        acceptable = bool(shapely.covers(parent.buffer(epsilon), child))
    return spill, limit, acceptable


def _independent_cell_overlap(geometries, values, source_domain, cell, origin):
    """Rebuild overlap from individual pre-block-union polygons at cell origin.

These polygons are source-derived, already country-clipped features. Each is
intersected with the local water before union; no positional buffer is used.
"""
    x, y = origin
    local_cell = translate(cell, xoff=-x, yoff=-y)
    water = translate(shapely.intersection(source_domain, cell), xoff=-x, yoff=-y)
    parts, labels = [], []
    for geometry, value in zip(geometries, values):
        if not shapely.intersects(geometry, cell):
            continue
        local = translate(geometry, xoff=-x, yoff=-y)
        parts.append(shapely.intersection(local, local_cell))
        labels.append(int(value))
    evidence = dict(coordinate_system='EPSG:3035 translated by minus origin_xy_m',
                    origin_xy_m=[x, y], cell_wkb_hex=local_cell.wkb_hex,
                    water_wkb_hex=water.wkb_hex,
                    source_cell_parts=[dict(multiplier=value, wkb_hex=g.wkb_hex)
                                       for g, value in zip(parts, labels)])
    try:
        clipped = [shapely.intersection(g, water) for g in parts]
        any_union = _polygon_union(clipped)
        ii_union = _polygon_union([g for g, value in zip(clipped, labels) if value == 30])
        tolerance = max(AREA_TOLERANCE_M2, water.area * 1e-9)
        # Rebuilt unions must meet the original containment tolerance before
        # final nesting. The larger recovery envelope is NOT used here.
        if (water.is_empty or water.area <= 0 or not water.is_valid
                or not any_union.is_valid or not ii_union.is_valid
                or shapely.difference(any_union, water).area > tolerance
                or shapely.difference(ii_union, any_union).area > tolerance):
            raise ValueError('Independent source-cell unions fail original containment checks')
        any_union = shapely.intersection(any_union, water)
        ii_union = shapely.intersection(ii_union, any_union)
        areas = np.array([water.area, any_union.area, ii_union.area], dtype='float64')
        if (not np.isfinite(areas).all() or np.any(areas < 0)
                or areas[1] > areas[0] + tolerance or areas[2] > areas[1] + tolerance):
            raise ValueError('Independent source-cell areas remain inconsistent')
        evidence['areas_m2'] = areas.tolist()
        return areas, evidence
    except (ValueError, shapely.errors.GEOSException) as exc:
        exc.cell_diagnostic = dict(independent_rebuild=evidence)
        raise


def _cell_area_error(message, bounds, water, any_union, ii_union, independent=None):
    error = ValueError(message)
    error.cell_diagnostic = dict(cell_bounds_epsg3035=bounds,
        coordinate_system='EPSG:3035 translated by minus cell upper-left',
        origin_xy_m=[bounds[0], bounds[3]], water_wkb_hex=water.wkb_hex,
        protected_any_wkb_hex=any_union.wkb_hex, category_II_wkb_hex=ii_union.wkb_hex,
        independent_rebuild=independent)
    return error


def _reconcile_cell_areas(domain, any_union, ii_union, shape, transform,
                          included, domain_area, any_area, ii_area, diagnostics=None,
                          source_geometries=None, source_values=None, source_domain=None):
    """Recompute inconsistent fast-path cells with direct nested intersections.

Independent overlays can disagree near polygon/pixel boundaries. Preserve
the original suspect/post-check tolerance, but permit tightly bounded boundary
residue before re-clipping to the unbuffered parent in local coordinates.
"""
    tolerance = np.maximum(AREA_TOLERANCE_M2, domain_area * 1e-9)
    suspect = included & ((any_area > domain_area + tolerance)
                          | (ii_area > any_area + tolerance))
    rows, cols = np.nonzero(suspect)
    if not len(rows):
        return
    if diagnostics is not None:
        diagnostics['direct_area_recomputed_cells'] = diagnostics.get('direct_area_recomputed_cells', 0) + len(rows)
    for start in range(0, len(rows), QUERY_CELLS):
        rr, cc = rows[start:start + QUERY_CELLS], cols[start:start + QUERY_CELLS]
        x, y = transform.c + cc*transform.a, transform.f + rr*transform.e
        cells = shapely.box(x, y+transform.e, x+transform.a, y)
        cell_water = shapely.intersection(cells, domain)
        cell_any = shapely.intersection(cells, any_union)
        cell_ii = shapely.intersection(cells, ii_union)
        for j, (row, col) in enumerate(zip(rr, cc)):
            bounds = [float(value) for value in shapely.bounds(cells[j])]
            epsilon = min(ROUNDOFF_DISTANCE_CAP_M,
                          max(1e-9, ROUNDOFF_ULPS * float(np.spacing(max(map(abs, bounds))))))
            # Translation reduces cancellation in subsequent overlays; the
            # original coordinate magnitude still sets the numerical allowance.
            local_water, local_any, local_ii = [translate(g, xoff=-float(x[j]), yoff=-float(y[j]))
                                                for g in (cell_water[j], cell_any[j], cell_ii[j])]
            direct_domain = float(local_water.area)
            direct_tolerance = max(AREA_TOLERANCE_M2, direct_domain * 1e-9)
            outside_water, water_limit, water_ok = _spill_recovery_limit(
                local_any, local_water, direct_domain, direct_tolerance, epsilon)
            outside_any, ii_limit, ii_ok = _spill_recovery_limit(
                local_ii, local_any, direct_domain, direct_tolerance, epsilon)
            area_ok = (math.isfinite(outside_water) and math.isfinite(outside_any)
                       and outside_water <= water_limit and outside_any <= ii_limit)
            needs_rebuild = not (water_ok and ii_ok)
            if not area_ok or (needs_rebuild and source_geometries is None):
                raise _cell_area_error(
                    'Protected union area exceeds its containing domain after direct geometry check: '
                    f'local row={int(row)}, col={int(col)}, cell_bounds={bounds}, '
                    f'water_m2={direct_domain:.12g}, protected_outside_water_m2={outside_water:.12g}, '
                    f'II_outside_any_protection_m2={outside_any:.12g}, '
                    f'tolerance_m2={direct_tolerance:.12g}, '
                    f'water_recovery_limit_m2={water_limit:.12g}, II_recovery_limit_m2={ii_limit:.12g}, '
                    f'boundary_distance_limit_m={epsilon:.12g}, '
                    f'water_spill_accepted={water_ok}, II_spill_accepted={ii_ok}',
                    bounds, local_water, local_any, local_ii)
            old_local_any, old_local_ii = local_any, local_ii
            # Actual area uses nested intersections with UNBUFFERED geometry.
            local_any = shapely.intersection(local_any, local_water)
            local_ii = shapely.intersection(local_ii, local_any)
            direct_any, direct_ii = float(local_any.area), float(local_ii.area)
            if needs_rebuild:
                nested_areas = np.array([direct_domain, direct_any, direct_ii])
                if not np.isfinite(nested_areas).all():
                    raise _cell_area_error('Non-finite nested block-union areas before independent rebuild',
                                          bounds, local_water, old_local_any, old_local_ii)
                try:
                    fresh_areas, evidence = _independent_cell_overlap(
                        source_geometries, source_values, source_domain,
                        cells[j], (float(x[j]), float(y[j])))
                except (ValueError, shapely.errors.GEOSException) as exc:
                    evidence = getattr(exc, 'cell_diagnostic', {}).get('independent_rebuild')
                    raise _cell_area_error(
                        f'Independent source-cell rebuild failed: local row={int(row)}, '
                        f'col={int(col)}, cell_bounds={bounds}: {exc}',
                        bounds, local_water, old_local_any, old_local_ii, evidence) from exc
                difference = float(np.max(np.abs(fresh_areas - nested_areas)))
                evidence['nested_block_union_areas_m2'] = nested_areas.tolist()
                evidence['max_area_disagreement_m2'] = difference
                if difference > direct_tolerance:
                    raise _cell_area_error(
                        'Independent source-cell rebuild disagrees with nested block-union overlap: '
                        f'local row={int(row)}, col={int(col)}, cell_bounds={bounds}, '
                        f'max_area_disagreement_m2={difference:.12g}, tolerance_m2={direct_tolerance:.12g}',
                        bounds, local_water, old_local_any, old_local_ii, evidence)
                direct_domain, direct_any, direct_ii = map(float, fresh_areas)
                if diagnostics is not None:
                    diagnostics['source_rebuilt_cells'] = diagnostics.get('source_rebuilt_cells', 0) + 1
                    diagnostics['max_independent_area_disagreement_m2'] = max(
                        diagnostics.get('max_independent_area_disagreement_m2', 0.0), difference)
            if (not np.isfinite([direct_domain, direct_any, direct_ii]).all()
                    or direct_domain <= 0 or direct_any < 0 or direct_ii < 0
                    or direct_any > direct_domain + direct_tolerance
                    or direct_ii > direct_any + direct_tolerance):
                raise _cell_area_error('Direct protected-area cell intersections remain inconsistent: '
                                      f'cell bounds={bounds}', bounds, local_water, old_local_any, old_local_ii)
            domain_area[row, col], any_area[row, col], ii_area[row, col] = direct_domain, direct_any, direct_ii
            if diagnostics is not None:
                if max(outside_water, outside_any) > direct_tolerance:
                    diagnostics['roundoff_recovered_cells'] = diagnostics.get('roundoff_recovered_cells', 0) + 1
                for key, value in (('max_protected_spill_m2', outside_water),
                                   ('max_II_spill_m2', outside_any),
                                   ('max_recovery_distance_m', epsilon)):
                    diagnostics[key] = max(diagnostics.get(key, 0.0), value)


def rasterise_block(geometries, values, shape, transform, domain_geometry=None,
                    included=None, return_areas=False, diagnostics=None):
    """Area mean of the pointwise maximum (1, 10, 30), capped at 30.

The denominator is cell intersection with domain_geometry (the unified water
buffer in production), or the full cell if no domain is supplied. Areas
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
            _reconcile_cell_areas(domain, any_union, ii_union, shape, transform,
                                  valid, domain_area, any_area, ii_area, diagnostics,
                                  source_geometries=geometries, source_values=values,
                                  source_domain=domain_geometry if domain_geometry is not None else footprint)
        tolerance = np.maximum(AREA_TOLERANCE_M2, domain_area * 1e-9)
        if (np.any(any_area > domain_area + tolerance)
                or np.any(ii_area > any_area + tolerance)):
            raise ValueError('Protected union area exceeds its containing domain after direct cell reconciliation')
        any_area = np.minimum(any_area, domain_area)
        ii_area = np.minimum(ii_area, any_area)
        # II is included in any_area: 1 + 9 + 20 = 30, never 30 * 10.
        result[valid] = (BACKGROUND + 9 * (any_area[valid] / domain_area[valid])
                         + 20 * (ii_area[valid] / domain_area[valid]))
        result[valid] = np.clip(result[valid], BACKGROUND, 30)
    if return_areas:
        return result, domain_area, any_area, ii_area
    return result



def raster_profile(grid):
    return dict(driver='GTiff', width=grid['width'], height=grid['height'], count=1,
                crs='EPSG:3035', transform=Affine(*grid['transform']), dtype='float32', nodata=NODATA,
                tiled=True, blockxsize=256, blockysize=256, compress='deflate', predictor=3, BIGTIFF='IF_SAFER')


def dependency_versions():
    result = {}
    for name in ('numpy', 'pandas', 'geopandas', 'pyogrio', 'shapely', 'pyproj', 'rasterio', 'affine'):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = 'unknown'
    result['GDAL'] = rasterio.__gdal_version__
    result['GEOS'] = shapely.geos_version_string
    return result


def _assert_country_unchanged(prepared, code):
    if any(file_state(path) != prepared['states'][name] for name, path in prepared['paths'].items()):
        raise RuntimeError(f'{code}: water-buffer input changed during run; retry with stable inputs')


def _write_block_failure(outdir, code, window, transform, included, water, geometries, values, error):
    """Keep a small, full-precision cell diagnostic after temporary files close."""
    folder = Path(outdir) / 'diagnostics'
    folder.mkdir(parents=True, exist_ok=True)
    row, col = int(window.row_off), int(window.col_off)
    path = folder / f'{code}_protected_area_block_{row}_{col}_failure.json'
    details = getattr(error, 'cell_diagnostic', None)
    if details is not None and details.get('cell_bounds_epsg3035'):
        # Save the inputs to the independent calculation, even if the area
        # budget rejected recovery before that calculation was attempted.
        bounds = details['cell_bounds_epsg3035']
        cell = shapely.box(*bounds)
        x, y = bounds[0], bounds[3]
        local_cell = translate(cell, xoff=-x, yoff=-y)
        try:
            details['original_domain_cell_wkb_hex'] = translate(
                shapely.intersection(water, cell), xoff=-x, yoff=-y).wkb_hex
            details['source_cell_parts'] = [
                dict(multiplier=int(value), wkb_hex=shapely.intersection(
                    translate(geometry, xoff=-x, yoff=-y), local_cell).wkb_hex)
                for geometry, value in zip(geometries, values) if shapely.intersects(geometry, cell)]
        except shapely.errors.GEOSException as capture_error:
            details['source_cell_capture_error'] = str(capture_error)
    payload = dict(schema_version=1, report_type='protected_area_failure_diagnostic',
                   country=code, created_utc=datetime.now(timezone.utc).isoformat(),
                   area_implementation=AREA_IMPLEMENTATION, error=str(error),
                   block=dict(row=row, col=col, height=int(window.height), width=int(window.width),
                              transform=list(window_transform(window, transform))[:6],
                              eligible_cells=int(included.sum()), source_candidate_count=len(geometries)),
                   cell=details, dependencies=dependency_versions(),
                   scope='Diagnostic only; no completed country raster/report is published on this failure',
                   geometry_encoding='Hex WKB preserves binary coordinate precision; local origins are explicitly recorded')
    with tempfile.TemporaryDirectory(prefix='diagnostic_', dir=folder) as temporary:
        staged = Path(temporary) / path.name
        staged.write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n')
        staged.replace(path)
    return path


def rasterise_country(args, code, prepared=None, sources=None):
    started = time.perf_counter()
    prepared = prepared or load_country_inputs(args, code)
    sources = sources if sources is not None else inspect_sources(args)
    _assert_country_unchanged(prepared, code)
    assert_sources_unchanged(sources)
    grid, transform, water = prepared['grid'], prepared['transform'], prepared['water']
    outdir = args.output_root/code/'raster'
    outdir.mkdir(parents=True, exist_ok=True)
    raster_name = f'{code}_water_buffer_protected_area_resistance_100m.tif'
    report_name = f'{code}_water_buffer_protected_area_resistance.json'
    counts = {key: 0 for key in ('eligible_cells', 'valid_cells', 'partial_domain_cells', 'unprotected_cells',
              'protected_any_cells', 'category_II_cells', 'other_protected_only_cells',
              'processed_blocks', 'rasterised_blocks', 'skipped_blocks', 'direct_area_recomputed_cells',
              'roundoff_recovered_cells', 'source_rebuilt_cells')}
    counts.update(max_protected_spill_m2=0.0, max_II_spill_m2=0.0, max_recovery_distance_m=0.0,
                  max_independent_area_disagreement_m2=0.0)
    classes = dict(unprotected=0, protected_without_II=0, includes_II=0)
    area_totals = {key: 0.0 for key in ('eligible_water', 'protected_any', 'category_II',
                                      'other_protected_only', 'unprotected')}
    minimum, maximum, value_sum = math.inf, -math.inf, 0.0
    total_blocks = math.ceil(grid['width']/args.block_size)*math.ceil(grid['height']/args.block_size)
    last_progress = time.perf_counter()
    print(f'{code}: exact protected-area means over water portions; '
          f'{prepared["expected_eligible_cells"]:,} eligible cells; {total_blocks:,} blocks', flush=True)
    with tempfile.TemporaryDirectory(prefix=f'{code}_protected_', dir=outdir) as temporary:
        stage = Path(temporary)
        frame, vector_report, cache_hit, cache_files = prepare_vectors(args, code, prepared, sources, stage)
        cache_names = (f'{code}_water_buffer_protected_area_polygons.gpkg',
                       f'{code}_water_buffer_protected_area_polygons.manifest.json')
        cache_states = {name: file_state(outdir/name) for name in cache_names} if cache_hit else {}
        geometries = frame.geometry.to_numpy()
        values = frame['multiplier'].to_numpy()
        tree = shapely.STRtree(geometries)
        with rasterio.open(prepared['paths']['mask']) as mask_source, \
                rasterio.open(stage/raster_name, 'w', **raster_profile(grid)) as writer:
            validate_mask_metadata(mask_source, grid, transform, code)
            for window in windows(grid['width'], grid['height'], args.block_size):
                included = _checked_mask(mask_source.read(1, window=window), water, transform, window, code)
                counts['processed_blocks'] += 1
                number = int(included.sum())
                counts['eligible_cells'] += number
                if not number:
                    counts['skipped_blocks'] += 1
                    writer.write(np.full(included.shape, NODATA, dtype='float32'), 1, window=window)
                    continue
                counts['rasterised_blocks'] += 1
                local_transform = window_transform(window, transform)
                footprint = shapely.box(local_transform.c, local_transform.f-included.shape[0]*100,
                                         local_transform.c+included.shape[1]*100, local_transform.f)
                selected = tree.query(footprint, predicate='intersects')
                previous_recomputed = counts['direct_area_recomputed_cells']
                try:
                    output, domain_area, any_area, ii_area = rasterise_block(
                        geometries[selected], values[selected], included.shape, local_transform,
                        domain_geometry=water, included=included, return_areas=True, diagnostics=counts)
                except (ValueError, shapely.errors.GEOSException) as exc:
                    failure_path = _write_block_failure(outdir, code, window, transform, included,
                                                         water, geometries[selected], values[selected], exc)
                    raise ValueError(f'{code}: block row={int(window.row_off)}, '
                                     f'col={int(window.col_off)}: {exc}; diagnostic: {failure_path}') from exc
                recomputed = counts['direct_area_recomputed_cells'] - previous_recomputed
                if recomputed:
                    print(f'{code}: directly recalculated protected/water areas for {recomputed} cells '
                          f'in block row={int(window.row_off)}, col={int(window.col_off)}', flush=True)
                if np.any(included & (domain_area <= 0)):
                    raise ValueError(f'{code}: eligible centre has zero exact water area; no output published')
                stored = output.astype('float32')
                valid_values = stored[included]
                if not np.isfinite(valid_values).all() or np.any((valid_values < 1) | (valid_values > 30)):
                    raise ValueError(f'{code}: resistance outside finite [1, 30] range')
                writer.write(stored, 1, window=window)
                counts['valid_cells'] += number
                counts['partial_domain_cells'] += int(np.count_nonzero(included & (domain_area < 10000-AREA_TOLERANCE_M2)))
                neutral = included & (any_area == 0)
                protected = included & (any_area > 0)
                ii = included & (ii_area > 0)
                other_area = np.maximum(0, any_area-ii_area)
                counts['unprotected_cells'] += int(neutral.sum())
                counts['protected_any_cells'] += int(protected.sum())
                counts['category_II_cells'] += int(ii.sum())
                counts['other_protected_only_cells'] += int(np.count_nonzero(included & (other_area > 0)))
                classes['unprotected'] += int(neutral.sum())
                classes['protected_without_II'] += int(np.count_nonzero(protected & ~ii))
                classes['includes_II'] += int(ii.sum())
                area_totals['eligible_water'] += float(domain_area.sum(dtype=np.float64))
                area_totals['protected_any'] += float(any_area.sum(dtype=np.float64))
                area_totals['category_II'] += float(ii_area.sum(dtype=np.float64))
                area_totals['other_protected_only'] += float(other_area.sum(dtype=np.float64))
                area_totals['unprotected'] += float(np.maximum(0, domain_area-any_area).sum(dtype=np.float64))
                minimum = min(minimum, float(valid_values.min()))
                maximum = max(maximum, float(valid_values.max()))
                value_sum += float(valid_values.sum(dtype=np.float64))
                if time.perf_counter()-last_progress >= 30:
                    print(f'{code}: {counts["processed_blocks"]:,}/{total_blocks:,} blocks; '
                          f'{counts["valid_cells"]:,} eligible cells written', flush=True)
                    last_progress = time.perf_counter()
            writer.update_tags(description='Exact water-area mean of maximum protected designation factor',
                               area_implementation=AREA_IMPLEMENTATION,
                               background='1', IUCN_II='30', other_protected_categories='10',
                               denominator='Exact cell intersection with unified vector water buffer',
                               eligibility=ELIGIBILITY, no_intersection='Neutral 1; mapped coverage not independently verified')
        if counts['eligible_cells'] != prepared['expected_eligible_cells'] or counts['valid_cells'] != counts['eligible_cells']:
            raise ValueError(f'{code}: completed raster count differs from validated domain')
        if sum(classes.values()) != counts['valid_cells']:
            raise RuntimeError(f'{code}: inconsistent output class counts')
        _assert_country_unchanged(prepared, code)
        assert_sources_unchanged(sources)
        if any(file_state(outdir/name) != state for name, state in cache_states.items()):
            raise RuntimeError(f'{code}: clipped-vector cache changed during rasterisation')
        number = counts['valid_cells']
        statistics = dict(count=number, minimum=minimum if number else None,
                          maximum=maximum if number else None, mean=value_sum/number if number else None)
        physical_area = area_totals['eligible_water']
        percentages = {key: (value/physical_area*100 if physical_area else None) for key, value in area_totals.items() if key != 'eligible_water'}
        inputs = dict(prepared['inputs'])
        inputs['protected_area_sources'] = [dict(path=str(s['path']), layer=s['layer'],
                                                  crs=s['crs'].to_string(), geometry_type=s['geometry_type'],
                                                  source_feature_count=s['feature_count'], components=s['metadata']) for s in sources]
        current_raster = file_metadata(stage/raster_name)
        current_raster['path'] = str(outdir/raster_name)
        outputs = dict(resistance=str(outdir/raster_name), report=str(outdir/report_name),
                       polygons=str(outdir/cache_names[0]), polygons_manifest=str(outdir/cache_names[1]))
        report = dict(schema_version=1, country=code, buffer_km=args.buffer_km,
                      created_utc=datetime.now(timezone.utc).isoformat(),
                      status='water_buffer_protected_area_prepared' if number else 'no_eligible_cell_centres',
                      grid=grid, inputs=inputs, outputs=outputs, raster_signature=current_raster,
                      rules=dict(eligibility=ELIGIBILITY, background=1, IUCN_II=30, other_protected_categories=10,
                                 category_normalisation='Trim surrounding whitespace and uppercase; missing values are other protected category',
                                 overlap='Pointwise maximum; union each category, category II takes priority',
                                 formula='1 + 9 * A_any / A_water + 20 * A_II / A_water',
                                 denominator='Exact cell intersection with unified vector water buffer',
                                 contact='Edge and point contacts contribute zero area',
                                 fraction_raster='Not read: diagnostic 10 m samples are not the area denominator or eligibility rule',
                                 realm_country_status_filter='None; all raw polygon realms, country attributes and designation statuses retained spatially',
                                 missing_category='Factor 10, never an unprotected fallback', nodata=NODATA,
                                 no_mapped_intersection='Factor 1 within eligible water; not proof of absence or complete source coverage'),
                      diagnostics=counts, statistics=statistics,
                      class_cells=classes,
                      class_percentages={name: (value/number*100 if number else None) for name, value in classes.items()},
                      class_partition='Mutually exclusive: no protected area, protected without II, or any II area',
                      diagnostic_count_overlap='protected_any includes category_II; other_protected_only_cells counts cells with positive non-II-only area and can overlap category_II_cells',
                      area_km2={name: value/1e6 for name, value in area_totals.items()},
                      area_percentages_of_eligible_water=percentages,
                      area_scope='Exact water portions of centre-eligible cells only; not the whole vector water area or full cell footprints',
                      area_partition='category_II + other_protected_only + unprotected = eligible_water; protected_any includes category_II',
                      vector_preparation=vector_report, vector_cache_reused=cache_hit,
                      coverage=dict(independent_protected_area_completeness_verified=False,
                                    numerical_values_available_percent=100.0 if number else None,
                                    note='A neutral value means no polygon intersection in supplied sources; it does not establish complete mapping or absence of designation'),
                      validation=dict(domain_gpkg_and_report_sha256_match_stage2=True,
                                      area_implementation=AREA_IMPLEMENTATION,
                                      area_consistency='Halo boundary candidates use exact intersections; inconsistent cells use local nested intersections; bounded distance failures require an independent per-feature cell rebuild',
                                      numerical_recovery=dict(
                                          original_absolute_tolerance_m2=AREA_TOLERANCE_M2,
                                          original_relative_tolerance=1e-9,
                                          coordinate_ulps=ROUNDOFF_ULPS,
                                          minimum_distance_m=1e-9,
                                          maximum_distance_m=ROUNDOFF_DISTANCE_CAP_M,
                                          additional_area_cap_m2=ROUNDOFF_AREA_CAP_M2,
                                          additional_relative_area_cap=ROUNDOFF_RELATIVE_AREA_CAP,
                                          area_limit='max(original tolerance, min(original tolerance + epsilon * sum of parent and child perimeters, area cap, relative cap * water area))',
                                          proximity='Spill beyond original tolerance must lie within epsilon, or pass an independent source-feature rebuild under the unchanged area budget',
                                          independent_rebuild='Clip individual pre-block-union source-derived features to original cell-water at a local origin, then union categories; require original containment tolerance and area agreement with nested block-union overlap',
                                          geometry='Buffers are validation-only; final areas use nested unbuffered intersections',
                                          note='Engineering recovery envelope, not a formal GEOS error bound; diagnostics record only directly recomputed cells'),
                                      mask_equals_strict_vector_centres_every_cell=True,
                                      eligible_count_matches_stage2=True,
                                      source_crs_geometry_category_and_companions_checked=True,
                                      raw_source_hashes='SHA-256 once per run; component identity/size/mtime/ctime checked for changes through publication',
                                      cache_integrity='Source content, current water-domain content and clipped GPKG SHA-256',
                                      atomicity='All country outputs staged before replacement; each file replacement atomic, report published last; not a multi-file transaction'),
                      processing=dict(block_size_100m=args.block_size, processing_seconds=time.perf_counter()-started,
                                      dependencies=dependency_versions(),
                                      memory='Blockwise raster arrays; spatial bbox candidates and clipped polygon parts are held in memory'),
                      integration_status='Standalone buffer factor only; other factors and optional composition remain separate')
        staged_report = stage/report_name
        staged_report.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        # Preserve all previous outputs until every source and raster check passes.
        for file in cache_files + [stage/raster_name, staged_report]:
            file.replace(outdir/file.name)
    print(f'{code}: protected-area resistance saved for {number:,} cells; '
          f'mean {statistics["mean"] if number else "n/a"}; {outdir/report_name}', flush=True)
    return report


def check_country_block(args, code, prepared=None, sources=None):
    """Run one production block without publishing a raster or vector cache.

Normal input validation and country-level polygon preparation still run. A
successful diagnostic applies only to the requested block, not the country.
"""
    started = time.perf_counter()
    if getattr(args, 'check_block', None) is None or args.countries != [code]:
        raise ValueError('A block check requires --check-block and exactly one country')
    row, col = args.check_block
    if any(value < 0 or value % args.block_size for value in (row, col)):
        raise ValueError('--check-block offsets must be nonnegative multiples of --block-size')
    prepared = prepared or load_country_inputs(args, code)
    grid, transform, water = prepared['grid'], prepared['transform'], prepared['water']
    if row >= grid['height'] or col >= grid['width']:
        raise ValueError(f'{code}: requested block is outside the {grid["height"]} by {grid["width"]} grid')
    sources = sources if sources is not None else inspect_sources(args)
    _assert_country_unchanged(prepared, code)
    assert_sources_unchanged(sources)
    window = Window(col, row, min(args.block_size, grid['width']-col),
                    min(args.block_size, grid['height']-row))
    local_transform = window_transform(window, transform)
    with rasterio.open(prepared['paths']['mask']) as mask_source:
        validate_mask_metadata(mask_source, grid, transform, code)
        included = _checked_mask(mask_source.read(1, window=window), water, transform, window, code)
    footprint = shapely.box(local_transform.c, local_transform.f+included.shape[0]*local_transform.e,
                            local_transform.c+included.shape[1]*local_transform.a, local_transform.f)
    outdir = args.output_root/code/'raster'
    diagnostics_dir = outdir/'diagnostics'
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    print(f'{code}: checking only block row={row}, col={col}; '
          'normal source validation and country polygon preparation still apply', flush=True)
    with tempfile.TemporaryDirectory(prefix=f'{code}_block_check_', dir=diagnostics_dir) as temporary:
        stage = Path(temporary)
        frame, vector_report, cache_hit, _ = prepare_vectors(args, code, prepared, sources, stage)
        cache_names = (f'{code}_water_buffer_protected_area_polygons.gpkg',
                       f'{code}_water_buffer_protected_area_polygons.manifest.json')
        cache_states = {name: file_state(outdir/name) for name in cache_names} if cache_hit else {}
        geometries = frame.geometry.to_numpy()
        values = frame['multiplier'].to_numpy()
        selected = shapely.STRtree(geometries).query(footprint, predicate='intersects')
        geometries, values = geometries[selected], values[selected]
        diagnostics = dict(direct_area_recomputed_cells=0, roundoff_recovered_cells=0,
                           source_rebuilt_cells=0, max_independent_area_disagreement_m2=0.0)
        try:
            output, domain_area, any_area, ii_area = rasterise_block(
                geometries, values, included.shape, local_transform,
                domain_geometry=water, included=included, return_areas=True, diagnostics=diagnostics)
            if (output.shape != included.shape or any(area.shape != included.shape
                    for area in (domain_area, any_area, ii_area))):
                raise ValueError('Block outputs differ from the requested mask shape')
            tolerance = np.maximum(AREA_TOLERANCE_M2, domain_area*1e-9)
            if (not all(np.isfinite(area).all() for area in (domain_area, any_area, ii_area))
                    or np.any(included & (domain_area <= 0))
                    or np.any(domain_area < 0) or np.any(any_area < 0) or np.any(ii_area < 0)
                    or np.any(any_area > domain_area+tolerance)
                    or np.any(ii_area > any_area+tolerance)):
                raise ValueError('Block areas are not finite and nested within the original area tolerance')
            if (not np.isfinite(output[included]).all()
                    or np.any((output[included] < 1) | (output[included] > 30))
                    or np.any(output[~included] != NODATA)):
                raise ValueError('Block resistance is outside finite [1, 30] or excluded cells are not NoData')
        except (ValueError, shapely.errors.GEOSException) as exc:
            _assert_country_unchanged(prepared, code)
            assert_sources_unchanged(sources)
            if any(file_state(outdir/name) != state for name, state in cache_states.items()):
                raise RuntimeError(f'{code}: clipped-vector cache changed during block check') from exc
            failure_path = _write_block_failure(outdir, code, window, transform, included,
                                                water, geometries, values, exc)
            raise ValueError(f'{code}: block row={row}, col={col}: {exc}; '
                             f'diagnostic saved to {failure_path}') from exc
        _assert_country_unchanged(prepared, code)
        assert_sources_unchanged(sources)
        if any(file_state(outdir/name) != state for name, state in cache_states.items()):
            raise RuntimeError(f'{code}: clipped-vector cache changed during block check')
        count = int(included.sum())
        valid = output[included]
        diagnostics.update(eligible_cells=count, valid_cells=int(valid.size),
                           candidate_polygon_parts=int(len(geometries)))
        report = dict(schema_version=1, status='block_check_passed', country=code,
                      created_utc=datetime.now(timezone.utc).isoformat(), buffer_km=args.buffer_km,
                      scope='Only this production block was checked; not a complete country raster validation',
                      area_implementation=AREA_IMPLEMENTATION,
                      block=dict(row_offset=int(row), column_offset=int(col),
                                 height=int(window.height), width=int(window.width),
                                 requested_block_size=args.block_size,
                                 transform=list(local_transform)[:6], bounds=list(footprint.bounds)),
                      diagnostics=diagnostics,
                      area_m2=dict(eligible_water=float(domain_area.sum()),
                                   protected_any=float(any_area.sum()), category_II=float(ii_area.sum())),
                      statistics=dict(count=count, minimum=float(valid.min()) if count else None,
                                      maximum=float(valid.max()) if count else None,
                                      mean=float(valid.mean()) if count else None),
                      validation=dict(strict_mask_centres=True, finite_factors_between_1_and_30=True,
                                      nested_areas_with_original_tolerance=True, unchanged_inputs=True),
                      inputs=dict(domain=prepared['inputs'],
                                  protected_area_sources=[dict(path=str(s['path']), layer=s['layer'],
                                                               components=s['metadata']) for s in sources]),
                      polygon_preparation=dict(cache_hit=cache_hit, report=vector_report),
                      processing=dict(seconds=time.perf_counter()-started, dependencies=dependency_versions()),
                      publication='Diagnostic JSON only; no resistance raster, country report, or polygon cache published')
        report_name = f'{code}_water_buffer_protected_area_block_r{row}_c{col}.json'
        staged_report = stage/report_name
        staged_report.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        staged_report.replace(diagnostics_dir/report_name)
    print(f'{code}: block check passed for row={row}, col={col}; '
          f'{count:,} eligible cells; {diagnostics_dir/report_name}', flush=True)
    return report


def main(argv=None):
    args = resolve_paths(build_parser().parse_args(argv))
    if tuple(int(part) for part in shapely.__version__.split('.')[:2]) < (2, 1):
        raise RuntimeError('This script requires shapely>=2.1')
    sources = inspect_sources(args)  # Large raw files are hashed once, shared by all countries.
    prepared = {}
    for code in args.countries:
        print(f'{code}: validating domain hashes, exact water geometry and mask metadata', flush=True)
        prepared[code] = load_country_inputs(args, code)
    assert_sources_unchanged(sources)
    # All country metadata pass preflight first. Full mask pixel/centre checks
    # run once during each country's staged raster processing before publication.
    for code in args.countries:
        if args.check_block is not None:
            check_country_block(args, code, prepared=prepared[code], sources=sources)
        else:
            rasterise_country(args, code, prepared=prepared[code], sources=sources)


if __name__ == '__main__':
    main()
