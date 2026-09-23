#!/usr/bin/env python3
"""Calculate EMODnet slope resistance only on an existing 100 m water buffer.

Install in data_processing/spatial_resistance/water_buffer/. Python >=3.10.
Dependencies: numpy, rasterio, affine, pyproj, netCDF4.
Inputs are local: no download, country-layer edits or population reads.

The gap-reason TIFF records 0=outside buffer, 1=valid slope, 2=outside
source support, 3=missing source corner, 4=nonnegative corner rejected by policy,
5=incomplete slope neighbourhood; 255 is reserved for NoData.
This diagnostic does not fill gaps or change the slope model.
Fixed-array NetCDF3 inputs are checked for truncated storage before processing;
NetCDF4/HDF5 completeness is not established by a file-size comparison.

Default --elevation-policy negative-only reproduces the conservative baseline.
Use --elevation-policy finite-only to compare finite elevations of either sign.
That comparison is provisional: source/shoreline provenance is not established.
Its default output folder is 5km_slope_finite_review, separate from the domain;
its sensitivity report can be selected by the updated integrator using --slope-root.
Missing values and incomplete neighbourhoods remain NoData in both modes.
The additional slope-validity mask marks computable cells (1), all others (0).
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import time

from affine import Affine
from netCDF4 import Dataset
import numpy as np
from pyproj import CRS, Transformer
import rasterio
from rasterio.windows import Window

SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SCRIPT_DIR.parents[2] / 'database' / 'pipeline'
COUNTRIES = ('NL', 'DE', 'NO')
TARGET_CRS = CRS.from_epsg(3035)
SOURCE_CRS = CRS.from_epsg(4326)
SOURCE_STEP_DEGREES = 1 / 960
NODATA = -9999.0
SLOPE_BINS = np.array([0, 5, 10, 20, 30, 45, 90], dtype=float)
GAP_REASON_CODES = {
    0: 'Outside eligible water buffer',
    1: 'Valid slope',
    2: 'Centre outside source interpolation support',
    3: 'Centre has missing or nonfinite source corner(s)',
    4: 'Centre rejected by negative-only policy: finite zero/positive source corner(s)',
    5: 'Centre passes screen but slope neighbourhood is incomplete',
    255: 'NoData or unwritten (absent from successful output)',
}
GAP_REASON_COLOURS = {
    0: (240, 240, 240, 255), 1: (35, 139, 69, 255),
    2: (84, 39, 143, 255), 3: (215, 48, 39, 255),
    4: (253, 174, 97, 255), 5: (49, 130, 189, 255),
    255: (0, 0, 0, 0),
}


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
    parser.add_argument('--bathymetry-dir', type=Path, help='directory containing COUNTRY_emodnet_dtm_2024.nc')
    parser.add_argument('--buffer-root', type=Path, help='scenario root containing COUNTRY/raster/')
    parser.add_argument('--output-root', type=Path,
                        help='scenario root for COUNTRY/raster/; default: buffer root for negative-only, separate review folder for finite-only')
    parser.add_argument('--elevation-policy', choices=('negative-only', 'finite-only'), default='negative-only',
                        help='negative-only: conservative baseline; finite-only: provisional comparison accepting either sign, requiring separate outputs')
    parser.add_argument('--buffer-km', type=positive_float, default=5.0)
    parser.add_argument('--block-size', type=block_size, default=128,
                        help='100 m cells per block edge (1..512; default: 128)')
    return parser


def resolve_paths(args):
    args.countries = list(dict.fromkeys(args.countries))
    args.pipeline_dir = args.pipeline_dir.expanduser().resolve()
    scenario = args.pipeline_dir / 'intermediate' / 'water_buffer' / f'{args.buffer_km:g}km'
    args.bathymetry_dir = (args.bathymetry_dir or args.pipeline_dir / 'raw' / 'bathymetry').expanduser().resolve()
    args.buffer_root = (args.buffer_root or scenario).expanduser().resolve()
    default_output = (args.buffer_root if args.elevation_policy == 'negative-only' else
                      args.buffer_root.parent / f'{args.buffer_km:g}km_slope_finite_review')
    args.output_root = (args.output_root or default_output).expanduser().resolve()
    if args.elevation_policy == 'finite-only' and args.output_root == args.buffer_root:
        raise ValueError('finite-only is a provisional comparison; use a separate --output-root rather than replacing the buffer production layers')
    return args


def input_paths(args, code):
    folder = args.buffer_root / code / 'raster'
    return dict(bathymetry=args.bathymetry_dir / f'{code}_emodnet_dtm_2024.nc',
                mask=folder / f'{code}_water_buffer_mask_100m.tif',
                domain_report=folder / f'{code}_water_buffer_raster.json')


def file_metadata(path):
    path = Path(path)
    digest = hashlib.sha256()
    before = path.stat()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f'Input changed while calculating its checksum: {path}')
    return dict(path=str(path.resolve()), size_bytes=after.st_size,
                mtime_ns=after.st_mtime_ns, sha256=digest.hexdigest())


def file_state(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def validate_grid(grid, label):
    if CRS.from_user_input(grid.get('crs')) != TARGET_CRS:
        raise ValueError(f'{label}: expected EPSG:3035')
    for name in ('width', 'height'):
        value = grid.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f'{label}: {name} must be a positive integer')
    transform = np.asarray(grid.get('transform'), dtype=float)
    if (transform.shape != (6,) or not np.isfinite(transform).all()
            or not np.allclose(transform[[0, 1, 3, 4]], [100, 0, 0, -100], atol=1e-8, rtol=0)):
        raise ValueError(f'{label}: expected finite north-up 100 m transform')
    fine = np.asarray(grid.get('fine_transform'), dtype=float)
    expected_fine = transform.copy()
    expected_fine[[0, 1, 3, 4]] /= 10
    if (grid.get('factor') != 10 or fine.shape != (6,) or not np.isfinite(fine).all()
            or not np.allclose(fine, expected_fine, atol=1e-8, rtol=0)
            or grid.get('fine_width') != 10*grid['width']
            or grid.get('fine_height') != 10*grid['height']):
        raise ValueError(f'{label}: declared 10 m grid must nest exactly in the 100 m grid')
    return Affine(*transform)


def json_value(value):
    if isinstance(value, np.ndarray):
        return [json_value(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (str, float, int, bool)) or value is None:
        return value
    return str(value)


def nc_attributes(variable):
    return {name: json_value(variable.getncattr(name)) for name in variable.ncattrs()}


# NetCDF3 format: https://docs.unidata.ucar.edu/nug/2.0-draft/nc3_file_formats.html
_CDF_TYPES = {
    1: ('NC_BYTE', 1), 2: ('NC_CHAR', 1), 3: ('NC_SHORT', 2),
    4: ('NC_INT', 4), 5: ('NC_FLOAT', 4), 6: ('NC_DOUBLE', 8),
    7: ('NC_UBYTE', 1), 8: ('NC_USHORT', 2), 9: ('NC_UINT', 4),
    10: ('NC_INT64', 8), 11: ('NC_UINT64', 8),
}
_CDF_MODELS = {1: 'NETCDF3_CLASSIC', 2: 'NETCDF3_64BIT_OFFSET', 5: 'NETCDF3_64BIT_DATA'}


class _CDFHeader:
    """Read header metadata only; seek over attribute payloads without loading."""
    def __init__(self, stream, size, version):
        self.stream, self.size, self.version = stream, size, version
        self.count_width = 8 if version == 5 else 4
        self.offset_width = 4 if version == 1 else 8

    def read(self, size):
        if size < 0 or self.stream.tell() + size > self.size:
            raise ValueError('Truncated NetCDF3 header')
        result = self.stream.read(size)
        if len(result) != size:
            raise ValueError('Truncated NetCDF3 header')
        return result

    def integer(self, width=4):
        return struct.unpack('>I' if width == 4 else '>Q', self.read(width))[0]

    def count(self):
        return self.integer(self.count_width)

    def skip(self, size):
        if size < 0 or self.stream.tell() + size > self.size:
            raise ValueError('Truncated NetCDF3 header attribute/name')
        self.stream.seek(size, os.SEEK_CUR)

    def name(self):
        length = self.count()
        # NetCDF names are small. This avoids allocating arbitrary data on a
        # corrupted count while leaving ample room for legitimate UTF-8 names.
        if not 0 < length <= 1024 * 1024:
            raise ValueError('Invalid NetCDF3 header name length')
        try:
            value = self.read(length).decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('Invalid UTF-8 name in NetCDF3 header') from exc
        self.skip((-length) % 4)
        return value

    def list_count(self, expected_tag):
        tag, count = self.integer(), self.count()
        if tag == 0 and count == 0:
            return 0
        if tag != expected_tag or count > (self.size - self.stream.tell()) // 4:
            raise ValueError('Invalid/truncated NetCDF3 header list')
        return count

    def type_info(self):
        code = self.integer()
        if code not in _CDF_TYPES or (self.version != 5 and code > 6):
            raise ValueError(f'Unsupported NetCDF3 external type {code}')
        return _CDF_TYPES[code]

    def attributes(self):
        for _ in range(self.list_count(12)):
            self.name()
            _, width = self.type_info()
            byte_count = self.count() * width
            self.skip(byte_count + (-byte_count) % 4)


def check_netcdf_storage(dataset):
    """Validate local fixed-array CDF byte extents; return reportable metadata.

    ``dataset`` is an already-open netCDF4.Dataset. Call before reading any
    coordinate or elevation arrays. HDF5/NetCDF4 compression means array logical
    size is not a physical file-length lower bound: this function reports that
    physical completeness is unverified for those formats, leaving normal
    netCDF/HDF5 reads and the caller's content checks in place.
    """
    path = Path(dataset.filepath())
    if not path.is_file():
        raise ValueError('NetCDF storage checks require a local regular file')
    before = path.stat()
    model = str(dataset.data_model)
    common = dict(format=model, file_size_bytes=before.st_size,
                  content_authenticity_verified=False,
                  limitation='Extent checks do not detect full-length zero-filled or sparse regions, prove bathymetry coverage, or verify a trusted source checksum')
    if model in {'NETCDF4', 'NETCDF4_CLASSIC'}:
        return dict(**common, check='netcdf4_open_only',
                    physical_extent_check_performed=False,
                    minimum_required_file_bytes=None,
                    status='physical_completeness_not_verified',
                    detail='Dataset opened through netCDF/HDF5; logical array bytes are not compared with compressed file length. Coordinate/sample reads occur in the caller; unvisited chunks are not checked.')
    with path.open('rb') as stream:
        signature = stream.read(4)
        if len(signature) != 4 or signature[:3] != b'CDF' or signature[3] not in _CDF_MODELS:
            raise ValueError('Unsupported NetCDF storage signature; expected CDF-1, CDF-2 or CDF-5')
        version = signature[3]
        if _CDF_MODELS[version] != model:
            raise ValueError('NetCDF header signature differs from the opened Dataset format')
        reader = _CDFHeader(stream, before.st_size, version)
        records = reader.count()
        dimensions = []
        for _ in range(reader.list_count(10)):
            name, length = reader.name(), reader.count()
            if length == 0:
                raise ValueError('NetCDF3 unlimited/record dimensions are unsupported by the fixed-array completeness check')
            dimensions.append((name, length))
        if records != 0:
            raise ValueError('NetCDF3 record counts are unsupported by the fixed-array completeness check')
        reader.attributes()
        variables = []
        for _ in range(reader.list_count(11)):
            name, rank = reader.name(), reader.count()
            if rank > (before.st_size - stream.tell()) // reader.count_width:
                raise ValueError('Invalid NetCDF3 variable rank in header')
            dimension_ids = [reader.count() for _ in range(rank)]
            if any(index >= len(dimensions) for index in dimension_ids):
                raise ValueError(f'Invalid NetCDF3 dimension ID for {name}')
            reader.attributes()
            type_name, width = reader.type_info()
            declared_vsize, begin = reader.count(), reader.integer(reader.offset_width)
            shape = [dimensions[index][1] for index in dimension_ids]
            payload_bytes = math.prod(shape) * width
            allocated_bytes = payload_bytes + (-payload_bytes) % 4
            # CDF-1/2 use a sentinel when the padded size exceeds the 32-bit
            # vsize limit. Compute from dimensions/types instead of trusting it.
            expected_vsize = (0xffffffff if version in (1, 2) and allocated_bytes > 0xfffffffc
                              else allocated_bytes)
            if declared_vsize != expected_vsize:
                raise ValueError(f'NetCDF3 variable {name}: declared vsize differs from dimensions/type')
            variables.append(dict(name=name, shape=shape, external_type=type_name,
                                  begin_offset_bytes=begin, payload_bytes=payload_bytes,
                                  padding_bytes=allocated_bytes - payload_bytes,
                                  required_end_bytes=begin + allocated_bytes))
        header_end = stream.tell()
    previous_end = header_end
    for variable in sorted(variables, key=lambda item: item['begin_offset_bytes']):
        if variable['begin_offset_bytes'] < previous_end or variable['begin_offset_bytes'] % 4:
            raise ValueError(f'NetCDF3 variable {variable["name"]}: overlapping or misaligned data offset')
        previous_end = variable['required_end_bytes']
    required = max([header_end] + [item['required_end_bytes'] for item in variables])
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('NetCDF file changed during the physical-extent check')
    if before.st_size < required:
        raise ValueError(f'Incomplete NetCDF3 file: {path.name} has {before.st_size:,} bytes; '
                         f'its declared fixed-variable extents require at least {required:,} bytes '
                         f'({required - before.st_size:,} bytes missing). Restore/redownload the complete source before computing slope.')
    return dict(**common, check='netcdf3_fixed_variable_extents',
                physical_extent_check_performed=True, status='declared_extents_present',
                signature=f'CDF-{version}', header_end_bytes=header_end,
                minimum_required_file_bytes=required, variables=variables)


def inspect_netcdf(dataset):
    """Validate physical storage, numerical elevation and 1D coordinates."""
    storage_check = check_netcdf_storage(dataset)
    if 'elevation' not in dataset.variables:
        raise ValueError('NetCDF requires numerical elevation(latitude, longitude), not a colour image or slope DN')
    variable = dataset.variables['elevation']
    if variable.dimensions != ('latitude', 'longitude') or variable.ndim != 2:
        raise ValueError('elevation must have dimensions (latitude, longitude)')
    if np.dtype(variable.dtype).kind not in 'fiu':
        raise ValueError('elevation must have a numeric storage type')
    units = str(getattr(variable, 'units', '')).strip().lower()
    if units not in {'m', 'metre', 'metres', 'meter', 'meters'}:
        raise ValueError('elevation must be measured in metres')
    long_name = str(getattr(variable, 'long_name', ''))
    if 'lowest astronomical tide' not in long_name.lower() and 'LAT' not in long_name.split():
        raise ValueError('elevation long_name must identify Lowest Astronomical Tide (LAT)')
    standard_name = str(getattr(variable, 'standard_name', ''))
    # The actual ERDDAP export calls this variable "depth" in standard_name,
    # but explicitly describes negative bathymetry and positive topography.
    # Honour that documented sign convention, not the misleading short name.
    sign_comment = str(getattr(variable, 'comment', '')).lower()
    documented_negative_depth = ('negative values for bathymetric depths' in sign_comment
                                 and 'positive values for topographic heights' in sign_comment)
    if standard_name and standard_name != 'height' and not (standard_name == 'depth' and documented_negative_depth):
        raise ValueError('Expected elevation with an explicit negative-bathymetry convention, not positive-down depth or encoded slope')
    positive = str(getattr(variable, 'positive', 'up')).lower()
    if positive != 'up':
        raise ValueError('Expected positive-up elevation, with negative values below LAT')
    axes = {}
    for name, expected_units in (('latitude', {'degrees_north', 'degree_north', 'degrees_n', 'degree_n'}),
                                 ('longitude', {'degrees_east', 'degree_east', 'degrees_e', 'degree_e'})):
        if name not in dataset.variables:
            raise ValueError(f'Missing {name} coordinate variable')
        axis = dataset.variables[name]
        if axis.dimensions != (name,) or axis.ndim != 1:
            raise ValueError(f'{name} must be a 1D coordinate variable')
        if str(getattr(axis, 'units', '')).lower() not in expected_units:
            raise ValueError(f'{name} must have angular {name} units')
        raw = np.ma.asarray(axis[:], dtype=np.float64)
        if np.ma.getmaskarray(raw).any():
            raise ValueError(f'{name} contains missing coordinates')
        values = np.asarray(raw)
        if values.size < 2 or not np.isfinite(values).all():
            raise ValueError(f'{name} requires at least two finite coordinates')
        differences = np.diff(values)
        direction = 1 if differences[0] > 0 else -1
        if (not np.all(direction * differences > 0)
                or not np.allclose(differences, direction * SOURCE_STEP_DEGREES, atol=1e-8, rtol=0)):
            raise ValueError(f'{name} must be regular, monotonic, native 1/960 degree (3.75 arc-second) coordinates')
        limit = 90 if name == 'latitude' else 180
        if np.min(values) < -limit or np.max(values) > limit:
            raise ValueError(f'{name} lies outside WGS84 angular limits')
        axes[name] = dict(values=values, ascending=direction > 0,
                          minimum=float(values.min()), maximum=float(values.max()),
                          count=int(values.size), step_degrees=float(differences[0]))
    if variable.shape != (axes['latitude']['count'], axes['longitude']['count']):
        raise ValueError('elevation shape differs from coordinate sizes')
    declared_crs = []
    attribute_sources = [dataset, variable]
    mapping_name = getattr(variable, 'grid_mapping', None)
    if mapping_name:
        if mapping_name not in dataset.variables:
            raise ValueError('elevation references an absent grid_mapping variable')
        mapping = dataset.variables[mapping_name]
        attribute_sources.append(mapping)
        mapping_type = getattr(mapping, 'grid_mapping_name', None)
        if mapping_type and mapping_type != 'latitude_longitude':
            raise ValueError('Expected a geographic latitude_longitude source grid')
    for source in attribute_sources:
        for name in ('crs', 'spatial_ref', 'crs_wkt', 'geospatial_bounds_crs', 'epsg_code', 'grid_mapping_epsg_code'):
            if name in source.ncattrs():
                declaration = source.getncattr(name)
                try:
                    candidate = CRS.from_user_input(declaration)
                except Exception as exc:
                    raise ValueError(f'Unrecognised declared source CRS in {name}: {declaration}') from exc
                if not candidate.equals(SOURCE_CRS, ignore_axis_order=True):
                    raise ValueError(f'Expected WGS84/EPSG:4326, found {declaration}')
                declared_crs.append(str(declaration))
    if not declared_crs:
        raise ValueError('NetCDF must explicitly declare WGS84/EPSG:4326; expected EMODnet grid_mapping_epsg_code')
    return dict(axes=axes, storage_check=storage_check,
                source_crs='EPSG:4326',
                crs_evidence='Explicit NetCDF CRS declaration checked',
                explicit_crs_declarations=declared_crs,
                elevation_attributes=nc_attributes(variable),
                latitude_attributes=nc_attributes(dataset.variables['latitude']),
                longitude_attributes=nc_attributes(dataset.variables['longitude']),
                global_attributes=nc_attributes(dataset),
                storage_dtype=str(variable.dtype),
                source_resolution_degrees=SOURCE_STEP_DEGREES,
                source_resolution_arc_seconds=3.75,
                source_resolution_arc_minutes=0.0625)


def _bracket(axis, requested):
    ascending = axis['values'] if axis['ascending'] else axis['values'][::-1]
    index = np.searchsorted(ascending, requested, side='right') - 1
    # The last grid coordinate has complete interpolation support from its left.
    index = np.where(requested == ascending[-1], len(ascending)-2, index)
    inside = (np.isfinite(requested) & (requested >= ascending[0])
              & (requested <= ascending[-1]) & (index >= 0) & (index < len(ascending)-1))
    safe = np.clip(index, 0, len(ascending)-2)
    weight = (requested-ascending[safe]) / (ascending[safe+1]-ascending[safe])
    first = safe if axis['ascending'] else len(ascending)-1-safe
    second = safe+1 if axis['ascending'] else len(ascending)-2-safe
    return first, second, weight, inside


def bilinear_sample(dataset, metadata, longitude, latitude, elevation_policy='negative-only'):
    """Sample bounded source windows; always require four finite corners.

    Four-corner validity is required even if a corner's interpolation weight is
    zero. The default rejects nonnegative corners. The explicit finite-only
    sensitivity accepts either sign without clamping or filling; neither policy
    proves bed origin. Shoreline/source suitability remains a separate assessment.
    """
    if elevation_policy not in ('negative-only', 'finite-only'):
        raise ValueError(f'Unknown elevation policy: {elevation_policy}')
    lon, lat = np.broadcast_arrays(np.asarray(longitude, dtype=float), np.asarray(latitude, dtype=float))
    r0, r1, fy, lat_ok = _bracket(metadata['axes']['latitude'], lat)
    c0, c1, fx, lon_ok = _bracket(metadata['axes']['longitude'], lon)
    inside = lat_ok & lon_ok
    result = {name: np.zeros(lon.shape, dtype=bool) for name in
              ('inside', 'finite_support', 'negative_support', 'accepted_support', 'source_zero_support', 'source_positive_support')}
    result['inside'] = inside
    result['elevation'] = np.full(lon.shape, np.nan, dtype=np.float64)
    if not inside.any():
        return result
    rows = np.concatenate((r0[inside], r1[inside]))
    cols = np.concatenate((c0[inside], c1[inside]))
    row_min, row_max = int(rows.min()), int(rows.max())
    col_min, col_max = int(cols.min()), int(cols.max())
    # Each target block spans at most 51.4 km plus halo. The corresponding source
    # read is a bounded rectangle; the whole national array is never loaded.
    raw = dataset.variables['elevation'][row_min:row_max+1, col_min:col_max+1]
    values = np.ma.asarray(raw, dtype=np.float64).filled(np.nan)
    a = values[r0[inside]-row_min, c0[inside]-col_min]
    b = values[r0[inside]-row_min, c1[inside]-col_min]
    c = values[r1[inside]-row_min, c0[inside]-col_min]
    d = values[r1[inside]-row_min, c1[inside]-col_min]
    corners = np.stack((a, b, c, d))
    finite = np.isfinite(corners).all(axis=0)
    negative = finite & (corners < 0).all(axis=0)
    result['finite_support'][inside] = finite
    result['negative_support'][inside] = negative
    accepted = negative if elevation_policy == 'negative-only' else finite
    result['accepted_support'][inside] = accepted
    result['source_zero_support'][inside] = np.any(corners == 0, axis=0)
    result['source_positive_support'][inside] = np.any(corners > 0, axis=0)
    x, y = fx[inside], fy[inside]
    sampled = (1-y)*((1-x)*a+x*b)+y*((1-x)*c+x*d)
    sampled[~accepted] = np.nan
    result['elevation'][inside] = sampled
    return result


def horn_slope(elevation_with_halo, valid_with_halo, cell_size=100.0):
    """Horn 3x3 slope in degrees; require all nine valid projected centres."""
    elevation = np.asarray(elevation_with_halo, dtype=np.float64)
    valid = np.asarray(valid_with_halo, dtype=bool)
    if elevation.ndim != 2 or min(elevation.shape) < 3 or elevation.shape != valid.shape:
        raise ValueError('Horn input requires matching 2D arrays with at least one interior cell')
    if not math.isfinite(cell_size) or cell_size <= 0:
        raise ValueError('cell_size must be finite and positive')
    complete = np.ones((elevation.shape[0]-2, elevation.shape[1]-2), dtype=bool)
    valid = valid & np.isfinite(elevation)
    for row in range(3):
        for column in range(3):
            complete &= valid[row:row+complete.shape[0], column:column+complete.shape[1]]
    dz_dx = ((elevation[:-2, 2:] + 2*elevation[1:-1, 2:] + elevation[2:, 2:])
             - (elevation[:-2, :-2] + 2*elevation[1:-1, :-2] + elevation[2:, :-2])) / (8*cell_size)
    dz_dy = ((elevation[2:, :-2] + 2*elevation[2:, 1:-1] + elevation[2:, 2:])
             - (elevation[:-2, :-2] + 2*elevation[:-2, 1:-1] + elevation[:-2, 2:])) / (8*cell_size)
    degrees = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
    complete &= np.isfinite(degrees)
    degrees[~complete] = np.nan
    return degrees, complete


def validate_mask_metadata(source, grid, transform, code):
    if (source.count != 1 or source.dtypes != ('uint8',) or source.nodata != 255
            or source.width != grid['width'] or source.height != grid['height']
            or source.crs is None or CRS.from_user_input(source.crs) != TARGET_CRS
            or not np.allclose(list(source.transform)[:6], list(transform)[:6], rtol=0, atol=1e-8)):
        raise ValueError(f'{code}: mask CRS, grid, dtype or NoData differs from required domain report')


def load_country_inputs(args, code):
    paths = input_paths(args, code)
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError('Required input files are missing:\n  '+'\n  '.join(missing))
    states = {name: file_state(path) for name, path in paths.items()}
    report = json.loads(paths['domain_report'].read_text())
    if report.get('schema_version') != 1 or report.get('country') != code:
        raise ValueError(f'{code}: expected schema 1 water-buffer raster report for this country')
    accepted = {'water_buffer_raster_domain_prepared', 'no_eligible_cell_centres', 'no_mapped_water_selected'}
    if report.get('status') not in accepted:
        raise ValueError(f'{code}: unexpected water-buffer raster status')
    distance = report.get('buffer_km')
    if isinstance(distance, bool) or not isinstance(distance, (int, float)) or not math.isclose(distance, args.buffer_km, abs_tol=1e-10, rel_tol=0):
        raise ValueError(f'{code}: requested buffer distance differs from input report')
    grid = report['grid']
    transform = validate_grid(grid, f'{code}/expanded grid')
    eligible = report.get('diagnostics', {}).get('eligible_cells')
    if isinstance(eligible, bool) or not isinstance(eligible, int) or not 0 <= eligible <= grid['width']*grid['height']:
        raise ValueError(f'{code}: report eligible_cells must be a nonnegative in-grid count')
    if (report['status'] == 'water_buffer_raster_domain_prepared') != (eligible > 0):
        raise ValueError(f'{code}: report status contradicts eligible cell count')
    rules = report.get('rules', {})
    if rules.get('excluded_mask') != 0 or rules.get('mask_nodata') != 255:
        raise ValueError(f'{code}: report must declare mask 0 excluded and 255 NoData')
    if rules.get('eligibility') != 'Strict 100 m cell centre lies inside combined vector water; exact polygon-boundary centres excluded':
        raise ValueError(f'{code}: unexpected water-buffer eligibility rule')
    with rasterio.open(paths['mask']) as source:
        validate_mask_metadata(source, grid, transform, code)
    with Dataset(paths['bathymetry']) as source:
        metadata = inspect_netcdf(source)
    if any(file_state(path) != states[name] for name, path in paths.items()):
        raise ValueError(f'{code}: input changed during preflight; rerun with stable inputs')
    return dict(paths=paths, report=report, grid=grid, transform=transform, source=metadata,
                expected_eligible_cells=eligible, preflight_file_states=states)


def windows(width, height, size):
    for row in range(0, height, size):
        for column in range(0, width, size):
            yield Window(column, row, min(size, width-column), min(size, height-row))


def target_coordinates(transform, window):
    # The halo extends beyond the output extent where needed; clipping here would
    # manufacture derivative gaps along raster rectangle boundaries.
    columns = np.arange(int(window.col_off)-1, int(window.col_off+window.width)+1) + 0.5
    rows = np.arange(int(window.row_off)-1, int(window.row_off+window.height)+1) + 0.5
    x = transform.c + transform.a*columns[None, :]
    y = transform.f + transform.e*rows[:, None]
    return np.broadcast_arrays(x, y)


def raster_profile(grid):
    return dict(driver='GTiff', width=grid['width'], height=grid['height'], count=1,
                crs='EPSG:3035', transform=Affine(*grid['transform']), dtype='float32', nodata=NODATA,
                tiled=True, blockxsize=256, blockysize=256, compress='deflate', predictor=3, BIGTIFF='IF_SAFER')


def dependency_versions():
    versions = {}
    for name in ('numpy', 'rasterio', 'affine', 'pyproj', 'netCDF4'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = 'unknown'
    versions['GDAL'] = rasterio.__gdal_version__
    return versions


def _summary(count, minimum, maximum, total):
    return dict(count=count, minimum=minimum if count else None,
                maximum=maximum if count else None, mean=total/count if count else None)


def rasterise_country(args, code, prepared=None):
    started = time.perf_counter()
    provisional = args.elevation_policy == 'finite-only'
    if provisional and args.output_root.resolve() == args.buffer_root.resolve():
        raise ValueError('finite-only requires a separate output root; production outputs must be preserved')
    prepared = prepared or load_country_inputs(args, code)
    grid, transform, paths = prepared['grid'], prepared['transform'], prepared['paths']
    if any(file_state(path) != prepared['preflight_file_states'][name] for name, path in paths.items()):
        raise ValueError(f'{code}: input changed since preflight; rerun with stable inputs')
    print(f'{code}: checksumming local inputs; EMODnet elevation at 3.75 arc-seconds', flush=True)
    provenance = {name: file_metadata(path) for name, path in paths.items()}
    if any((item['size_bytes'], item['mtime_ns']) != prepared['preflight_file_states'][name]
           for name, item in provenance.items()):
        raise ValueError(f'{code}: input changed since preflight while preparing checksums; rerun with stable inputs')
    if json.loads(paths['domain_report'].read_text()) != prepared['report']:
        raise ValueError(f'{code}: water-buffer report changed since preflight; rerun with stable inputs')
    outdir = args.output_root / code / 'raster'
    outdir.mkdir(parents=True, exist_ok=True)
    names = dict(slope_degrees=f'{code}_water_buffer_slope_degrees_100m.tif',
                 slope_resistance=f'{code}_water_buffer_slope_resistance_100m.tif',
                 gap_reason=f'{code}_water_buffer_slope_gap_reason_100m.tif',
                 valid_mask=f'{code}_water_buffer_slope_valid_mask_100m.tif',
                 report=f'{code}_water_buffer_slope.json')
    gap_profile = raster_profile(grid)
    gap_profile.update(dtype='uint8', nodata=255, predictor=1)
    counts = {key: 0 for key in ('eligible_cells', 'centre_outside_source_support_cells',
              'centre_missing_source_cells', 'centre_nonnegative_source_cells',
              'centre_finite_source_support_cells', 'centre_usable_negative_source_cells',
              'centre_accepted_source_cells', 'centre_rejected_source_cells',
              'incomplete_slope_neighbourhood_cells', 'valid_slope_cells',
              'valid_slope_cells_with_negative_only_support', 'valid_slope_cells_requiring_nonnegative_support',
              'centre_source_zero_support_cells', 'centre_source_positive_support_cells',
              'processed_blocks', 'sampled_blocks', 'skipped_blocks')}
    bins = np.zeros(len(SLOPE_BINS)-1, dtype=np.int64)
    slope_sum, factor_sum = 0.0, 0.0
    slope_min, slope_max, factor_min, factor_max = math.inf, -math.inf, math.inf, -math.inf
    coordinate_transformer = Transformer.from_crs(TARGET_CRS, SOURCE_CRS, always_xy=True)
    total_blocks = math.ceil(grid['width']/args.block_size)*math.ceil(grid['height']/args.block_size)
    print(f'{code}: buffer slope; {grid["width"]:,} x {grid["height"]:,} cells; '
          f'{prepared["expected_eligible_cells"]:,} eligible; {total_blocks:,} blocks', flush=True)
    last_progress = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f'{code}_slope_', dir=outdir) as directory:
        stage = Path(directory)
        with Dataset(paths['bathymetry']) as source, rasterio.open(paths['mask']) as mask_source, \
                rasterio.open(stage/names['slope_degrees'], 'w', **raster_profile(grid)) as slope_writer, \
                rasterio.open(stage/names['slope_resistance'], 'w', **raster_profile(grid)) as resistance_writer, \
                rasterio.open(stage/names['gap_reason'], 'w', **gap_profile) as gap_writer, \
                rasterio.open(stage/names['valid_mask'], 'w', **gap_profile) as valid_writer:
            validate_mask_metadata(mask_source, grid, transform, code)
            source.set_auto_maskandscale(True)
            # Recheck metadata immediately before using it, rather than relying
            # on an earlier preflight if another input was prepared meanwhile.
            metadata = inspect_netcdf(source)
            for window in windows(grid['width'], grid['height'], args.block_size):
                mask = mask_source.read(1, window=window)
                if not np.isin(mask, [0, 1]).all():
                    raise ValueError(f'{code}: completed domain mask must contain only 0 and 1; found invalid/NoData cells')
                eligible = mask == 1
                counts['eligible_cells'] += int(eligible.sum())
                degree_output = np.full(mask.shape, NODATA, dtype='float32')
                factor_output = np.full(mask.shape, NODATA, dtype='float32')
                gap_output = np.zeros(mask.shape, dtype='uint8')
                gap_output[eligible] = 255
                valid_output = np.zeros(mask.shape, dtype='uint8')
                if eligible.any():
                    counts['sampled_blocks'] += 1
                    x, y = target_coordinates(transform, window)
                    lon, lat = coordinate_transformer.transform(x, y)
                    sampled = bilinear_sample(source, metadata, lon, lat, elevation_policy=args.elevation_policy)
                    centre = {name: values[1:-1, 1:-1] for name, values in sampled.items()}
                    inside = centre['inside']
                    finite = centre['finite_support']
                    negative = centre['negative_support']
                    accepted = centre['accepted_support']
                    counts['centre_outside_source_support_cells'] += int((eligible & ~inside).sum())
                    counts['centre_missing_source_cells'] += int((eligible & inside & ~finite).sum())
                    counts['centre_nonnegative_source_cells'] += int((eligible & finite & ~negative).sum())
                    counts['centre_finite_source_support_cells'] += int((eligible & finite).sum())
                    counts['centre_usable_negative_source_cells'] += int((eligible & negative).sum())
                    counts['centre_accepted_source_cells'] += int((eligible & accepted).sum())
                    counts['centre_rejected_source_cells'] += int((eligible & finite & ~accepted).sum())
                    counts['centre_source_zero_support_cells'] += int((eligible & centre['source_zero_support']).sum())
                    counts['centre_source_positive_support_cells'] += int((eligible & centre['source_positive_support']).sum())
                    degrees, complete = horn_slope(sampled['elevation'], sampled['accepted_support'])
                    usable = eligible & complete
                    valid_output[usable] = 1
                    negative_neighbourhood = np.ones(complete.shape, dtype=bool)
                    for offset_row in range(3):
                        for offset_col in range(3):
                            negative_neighbourhood &= sampled['negative_support'][offset_row:offset_row+mask.shape[0], offset_col:offset_col+mask.shape[1]]
                    counts['valid_slope_cells_with_negative_only_support'] += int((usable & negative_neighbourhood).sum())
                    counts['valid_slope_cells_requiring_nonnegative_support'] += int((usable & ~negative_neighbourhood).sum())
                    gap_output[eligible & ~inside] = 2
                    gap_output[eligible & inside & ~finite] = 3
                    gap_output[eligible & finite & ~accepted] = 4
                    gap_output[eligible & accepted & ~complete] = 5
                    gap_output[usable] = 1
                    counts['incomplete_slope_neighbourhood_cells'] += int((eligible & accepted & ~complete).sum())
                    n = int(usable.sum())
                    counts['valid_slope_cells'] += n
                    if n:
                        slope_values = degrees[usable]
                        factors = 1 + 19*slope_values/90
                        degree_output[usable] = slope_values.astype('float32')
                        factor_output[usable] = factors.astype('float32')
                        bins += np.histogram(slope_values, bins=SLOPE_BINS)[0]
                        slope_sum += float(slope_values.sum(dtype=np.float64))
                        factor_sum += float(factors.sum(dtype=np.float64))
                        slope_min, slope_max = min(slope_min, float(slope_values.min())), max(slope_max, float(slope_values.max()))
                        factor_min, factor_max = min(factor_min, float(factors.min())), max(factor_max, float(factors.max()))
                else:
                    counts['skipped_blocks'] += 1
                slope_writer.write(degree_output, 1, window=window)
                resistance_writer.write(factor_output, 1, window=window)
                if np.any(gap_output == 255):
                    raise RuntimeError('Internal gap-reason classification error')
                gap_writer.write(gap_output, 1, window=window)
                valid_writer.write(valid_output, 1, window=window)
                counts['processed_blocks'] += 1
                if time.perf_counter()-last_progress >= 20:
                    print(f'{code}: {counts["processed_blocks"]:,}/{total_blocks:,} blocks; '
                          f'{counts["valid_slope_cells"]:,} cells with computable slope', flush=True)
                    last_progress = time.perf_counter()
            if counts['eligible_cells'] != prepared['expected_eligible_cells']:
                raise ValueError(f'{code}: actual mask eligibility count differs from domain report')
            common = dict(country=code, stage='water_buffer_slope', buffer_km=str(args.buffer_km),
                          source='EMODnet DTM2024 elevation relative to LAT',
                          elevation_policy=args.elevation_policy,
                          result_use='provisional sensitivity only' if provisional else 'conservative baseline',
                          sampling=('Bilinear 100 m centres; all four source values finite; either sign accepted' if provisional else
                                    'Bilinear 100 m centres; all four source values finite and strictly negative'),
                          slope='Horn 3x3; all nine sampled centres valid; nominal 100 m projected spacing',
                          missing_policy='NoData retained; no flat or neutral fill', bathymetry_origin='Not established by sign or numeric coverage')
            slope_writer.update_tags(**common, units='degrees', description='Underwater terrain slope proxy on eligible water-buffer cells')
            resistance_writer.update_tags(**common, units='dimensionless', formula='1 + 19 * slope_degrees / 90',
                                          description='Assumed terrestrial-to-underwater slope multiplier transfer')
            gap_writer.write_colormap(1, GAP_REASON_COLOURS)
            gap_writer.update_tags(**common, units='category',
                                   description='Disjoint slope availability and gap reasons; 0 is a valid outside-buffer category',
                                   category_codes=json.dumps(GAP_REASON_CODES, sort_keys=True),
                                   precedence='Outside buffer, outside source, missing source, rejected by elevation policy, incomplete neighbourhood, valid')
            valid_writer.update_tags(**common, units='boolean',
                                     description='1 = computable slope under selected policy; 0 = outside buffer or unavailable; 255 = NoData/unwritten',
                                     interpretation='Numeric availability only; not proof of bed provenance or a complete routing-validity mask')
        # Any in-process file size/mtime change invalidates the staged result;
        # mtime differences between separate runs do not invalidate provenance.
        for name, path in paths.items():
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (provenance[name]['size_bytes'], provenance[name]['mtime_ns']):
                raise ValueError(f'{code}: {name} changed during processing; previous outputs preserved')
        n, eligible_count = counts['valid_slope_cells'], counts['eligible_cells']
        rejected = sum(counts[key] for key in ('centre_outside_source_support_cells', 'centre_missing_source_cells',
                                               'centre_rejected_source_cells', 'incomplete_slope_neighbourhood_cells'))
        if (rejected+n != eligible_count or int(bins.sum()) != n
                or counts['valid_slope_cells_with_negative_only_support'] + counts['valid_slope_cells_requiring_nonnegative_support'] != n):
            raise RuntimeError('Internal coverage accounting error')
        status = ('no_eligible_buffer_cells' if not eligible_count else
                  'no_computable_buffer_slope' if not n else
                  'water_buffer_slope_prepared' if n == eligible_count else 'water_buffer_slope_prepared_with_gaps')
        source_report = {key: value for key, value in metadata.items() if key != 'axes'}
        source_report['axes'] = {name: {key: value for key, value in axis.items() if key != 'values'}
                                 for name, axis in metadata['axes'].items()}
        report = dict(schema_version=1, report_type='water_buffer_slope_sensitivity' if provisional else 'water_buffer_slope', country=code,
                      elevation_policy=args.elevation_policy, production_integration_eligible=not provisional,
                      buffer_km=args.buffer_km, created_utc=datetime.now(timezone.utc).isoformat(), status=status,
                      grid=grid, inputs=provenance, source=source_report,
                      rules=dict(eligibility='Input mask equals 1; eligibility and partial-cell rules unchanged',
                                 source_screen=('Every bilinear source corner finite; either sign accepted provisionally; no missing-value renormalisation' if provisional else
                                                'Every bilinear source corner finite and strictly negative relative to LAT; no missing-value renormalisation'),
                                 nonnegative_interpretation=('Accepted as a sensitivity assumption; bed/intertidal provenance not established, including for neighbours outside the water buffer' if provisional else
                                                            'Excluded conservatively; zero/positive values are not proof of land or lack of bed data'),
                                 source_support='Four neighbours required even where a bilinear weight is zero',
                                 interpolation='Bilinear elevation to target 100 m cell centres',
                                 derivative='Horn 3x3; all nine target elevations required',
                                 halo='One target cell; neighbourhood may extend outside buffer and output rectangle',
                                 mask_order='Calculate derivative with surrounding elevations before applying eligible buffer mask',
                                 horizontal_distance='Nominal EPSG:3035 projected metres, 100 m in each axis',
                                 multiplier_formula='1 + 19 * slope_degrees / 90',
                                 multiplier_assumption='Transfer of terrestrial slope penalty to underwater terrain; not a calibrated offshore cost relationship',
                                 missing_data='NoData; never replace with zero slope or multiplier 1', output_nodata=NODATA,
                                 usable_mask='Slope-validity mask is 1 only for computable slope; original water-buffer domain remains unchanged',
                                 integration='Not integrated; original country layers unmodified'),
                      coverage=dict(**counts, missing_slope_cells=eligible_count-n,
                                    valid_slope_percent=(100*n/eligible_count if eligible_count else None),
                                    full_numeric_slope_coverage=(n == eligible_count if eligible_count else None),
                                    bathymetry_origin_verified=False,
                                    partition='eligible = centre outside support + centre missing support + centre rejected by elevation policy + incomplete neighbourhood + valid slope',
                                    diagnostic_overlap='Nonnegative-source counts are descriptive, not exclusion counts in finite-only mode; zero/positive corner counts can overlap each other and missing-source counts'),
                      gap_diagnostics=dict(output=names['gap_reason'], codes=GAP_REASON_CODES,
                                           nodata=255, outside_buffer_code=0,
                                           interpretation='One disjoint reason per cell; centre failures take precedence over neighbourhood failures. Nonnegative elevations are not proof of land.',
                                           counts={0: grid['width']*grid['height']-eligible_count,
                                                   1: n, 2: counts['centre_outside_source_support_cells'],
                                                   3: counts['centre_missing_source_cells'],
                                                   4: counts['centre_rejected_source_cells'],
                                                   5: counts['incomplete_slope_neighbourhood_cells'], 255: 0}),
                      statistics=dict(slope_degrees=_summary(n, slope_min, slope_max, slope_sum),
                                      slope_resistance=_summary(n, factor_min, factor_max, factor_sum),
                                      scope='Eligible cells with computable slope only; statistics use float64 calculations before float32 storage',
                                      slope_classes=[dict(lower_degrees=float(SLOPE_BINS[i]), upper_degrees=float(SLOPE_BINS[i+1]),
                                                          upper_inclusive=(i == len(bins)-1), cells=int(count),
                                                          percent_of_valid=(100*int(count)/n if n else None)) for i, count in enumerate(bins)]),
                      limitations=[
                          'NetCDF3 declared byte extents detect truncation, not full-length zero-filled corruption or untrusted content. NetCDF4/HDF5 completeness is not verified; see source.storage_check.',
                          'Numeric negative elevations do not establish marine/inland seabed survey coverage; source provenance and lake/river bed coverage need separate assessment.',
                          ('Finite-only accepts zero/positive elevations provisionally, including possible land or water-surface contamination; validate source regions and interpolation/derivative support before any production use.' if provisional else
                           'Coastal zero/positive elevations and missing neighbours are conservatively excluded, including possibly valid intertidal data.'),
                          'Native angular spacing is 3.75 arc-seconds; resampling to 100 m adds no source detail; source resolution, interpolation and derivative smoothing affect slope.',
                          'EPSG:3035 is equal-area, not equidistant; nominal projected distances introduce spatially varying slope distortion, particularly toward projection margins.',
                          'LAT is the source vertical reference; spatial changes in vertical reference or source mosaicking may introduce gradients.',
                          'Stage-2 report does not hash its output mask; dimensions, counts and metadata are checked and current hashes recorded, but historical mask identity cannot be proven.',
                          'Each output replacement is atomic, but the five-file set is not one filesystem transaction; the report is replaced last.',
                          'Original-country raster priority, routing-edge containment and integrated missing-data behaviour remain later steps.'],
                      remaining_layers=['protected_areas', 'motorways', 'railways', 'pipelines'],
                      integration_status=('Finite-only sensitivity output; supported by the updated integrator via --slope-root; source provenance remains unverified' if provisional else
                                          'Not integrated; inspect bathymetry provenance and any missing slope before combining layers'),
                      processing=dict(block_size_100m=args.block_size, processing_seconds=time.perf_counter()-started,
                                      dependencies=dependency_versions()),
                      outputs={name: str((outdir/filename).resolve()) for name, filename in names.items()})
        (stage/names['report']).write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        for name in ('slope_degrees', 'slope_resistance', 'gap_reason', 'valid_mask', 'report'):
            (stage/names[name]).replace(outdir/names[name])
    percentage = f'{100*n/eligible_count:.4f}%' if eligible_count else 'n/a'
    print(f'{code}: {n:,}/{eligible_count:,} eligible cells with computable slope ({percentage}); '
          f'{eligible_count-n:,} retained as NoData; {outdir/names["report"]}', flush=True)
    return report


def main(argv=None):
    parser = build_parser()
    args = resolve_paths(parser.parse_args(argv))
    print(f'Water-buffer slope: countries {", ".join(args.countries)}; buffer {args.buffer_km:g} km; '
          f'policy {args.elevation_policy}; script {Path(__file__).resolve()}', flush=True)
    if args.elevation_policy == 'finite-only':
        print(f'Provisional finite-elevation comparison: outputs {args.output_root}; missing elevations remain excluded. '
              'Source/shoreline suitability requires validation before production integration.', flush=True)
    # Validate the metadata for every requested country before replacing any
    # country outputs. Pixel-value/count validation occurs during processing.
    prepared = {code: load_country_inputs(args, code) for code in args.countries}
    for code in args.countries:
        rasterise_country(args, code, prepared[code])


if __name__ == '__main__':
    main()
