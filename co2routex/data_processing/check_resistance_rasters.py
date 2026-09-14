"""Blockwise resistance checks and optional lossless NoData-margin cropping.

Dependencies: pip install numpy rasterio pyproj
Default: create/verify crops. Set WRITE_CROPPED = False for report only.
Creates new *_cropped.tif
files where margins can be removed. Originals are never overwritten.
No resampling, snapping, resistance rounding or domain restriction is applied.
"""
from pathlib import Path
import math
import re
from uuid import uuid4
import numpy as np
import rasterio
from pyproj import CRS
from rasterio.windows import Window

RASTER_DIR = Path(__file__).resolve().parent.parent / "database/pipeline/spatial_cost_resistance"
RASTER_PATHS = [RASTER_DIR / f"spatial_resistance_{c}.tif" for c in ("NL", "DE", "NO")]
WRITE_CROPPED = True


def equivalent_raster_crs(source_crs, output_crs):
    """Allow only tiny inverse-flattening serialization noise as a fallback.

    This changes a comparison copy, never the actual raster CRS. All other
    CRS properties must still pass PROJ's semantic equivalence check.
    """
    if source_crs.equals(output_crs, ignore_axis_order=True):
        return True
    # PROJ's ignore_axis_order does not consistently normalize projected axes.
    # For these 2-D east/north rasters only, compare explicit x/y axis copies.
    def xy_copy(crs):
        if not crs.is_projected:
            return None
        obj = crs.to_json_dict()
        cs = obj.get("coordinate_system", {})
        axes = cs.get("axis", [])
        if cs.get("subtype") != "Cartesian" or len(axes) != 2:
            return None
        by_direction = {axis["direction"]: axis for axis in axes}
        if set(by_direction) != {"east", "north"}:
            return None
        cs["axis"] = [by_direction["east"], by_direction["north"]]
        return CRS.from_json_dict(obj)
    source_crs, output_crs = xy_copy(source_crs), xy_copy(output_crs)
    if source_crs is None or output_crs is None:
        return False
    if source_crs.equals(output_crs, ignore_axis_order=True):
        print("Accepted projected axis metadata ordering difference (raster x/y unchanged)")
        return True
    source_inverse = source_crs.ellipsoid.inverse_flattening
    output_inverse = output_crs.ellipsoid.inverse_flattening
    delta = abs(source_inverse - output_inverse)
    if not math.isfinite(delta) or delta > 1e-10:
        return False
    # Modify only the inverse flattening inside the sole ellipsoid clause.
    pattern = r'((?:ELLIPSOID|SPHEROID)\s*\[\s*"[^"]*"\s*,\s*[^,]+,\s*)([^,\]]+)'
    wkt, replacements = re.subn(
        pattern, lambda match: match.group(1) + repr(output_inverse),
        source_crs.to_wkt(), flags=re.IGNORECASE)
    if replacements != 1:
        return False
    equivalent = CRS.from_wkt(wkt).equals(output_crs, ignore_axis_order=True)
    if equivalent:
        print(f"Accepted inverse-flattening serialization difference: {delta:.12g} (limit 1e-10)")
    return equivalent


def verify_crop(src, crop, target):
    """Check equivalent CRS, sub-micrometre geometry, exact pixels and masks."""
    with rasterio.open(target) as dst:
        expected = src.window_transform(crop)
        print(f"Verification: {target.name}")
        print(f"Source CRS: {src.crs}")
        print(f"Output CRS: {dst.crs}")
        if src.crs is None or dst.crs is None:
            raise ValueError("Verification failed: missing CRS")
        source_crs = CRS.from_wkt(src.crs.to_wkt())
        output_crs = CRS.from_wkt(dst.crs.to_wkt())
        # GeoTIFF raster transforms explicitly map columns/rows to x/y.
        # CRS axis-order metadata can change on serialization without moving pixels.
        equivalent = equivalent_raster_crs(source_crs, output_crs)
        print(f"Equivalent CRS (axis order and bounded ellipsoid rounding): {equivalent}")
        if not equivalent:
            raise ValueError(
                "Verification failed: CRS equivalence not established.\n"
                f"SOURCE WKT: {src.crs.to_wkt()}\nOUTPUT WKT: {dst.crs.to_wkt()}"
            )
        if (dst.width, dst.height, dst.count, dst.dtypes) != (
                int(crop.width), int(crop.height), 1, src.dtypes):
            raise ValueError("Verification failed: dimensions, band count or dtype differ")
        corner_errors = []
        for col, row in ((0, 0), (dst.width, 0), (0, dst.height),
                         (dst.width, dst.height)):
            ex, ey = expected * (col, row)
            ax, ay = dst.transform * (col, row)
            corner_errors.append(math.hypot(ax-ex, ay-ey))
        error = max(corner_errors)
        # These resistance datasets use projected metre coordinates.
        print(f"Expected transform: {expected!r}")
        print(f"Output transform:   {dst.transform!r}")
        print(f"Maximum corner displacement: {error:.12g} coordinate units")
        if not math.isfinite(error) or error > 1e-6:
            raise ValueError("Verification failed: crop georeferencing differs by more than 1e-6 coordinate units")
        same_nodata = dst.nodata == src.nodata or (
            dst.nodata is not None and src.nodata is not None
            and math.isnan(dst.nodata) and math.isnan(src.nodata))
        if not same_nodata or dst.scales != src.scales or dst.offsets != src.offsets:
            raise ValueError("Verification failed: NoData, scale or offset changed")
        for _, outwin in dst.block_windows(1):
            inwin = Window(crop.col_off+outwin.col_off, crop.row_off+outwin.row_off,
                           outwin.width, outwin.height)
            if not np.array_equal(dst.read(1, window=outwin),
                                  src.read(1, window=inwin), equal_nan=True):
                raise ValueError(f"Verification failed: pixel values differ at {outwin}")
            if not np.array_equal(dst.read_masks(1, window=outwin),
                                  src.read_masks(1, window=inwin)):
                raise ValueError(f"Verification failed: masks differ at {outwin}")


def inspect(src):
    if src.count != 1 or src.dtypes[0] != "float32":
        raise ValueError("Expected a single-band Float32 resistance raster")
    top, left, bottom, right = src.height, src.width, -1, -1
    count = invalid_finite_count = negative_count = zero_count = 0
    minimum, maximum = math.inf, -math.inf
    for _, win in src.block_windows(1):
        data = src.read(1, window=win, masked=True)
        unmasked = ~np.ma.getmaskarray(data)
        finite = np.isfinite(data.data)
        invalid_finite_count += int(np.count_nonzero(unmasked & ~finite))
        valid = unmasked & finite
        rows, cols = np.nonzero(valid)
        if not rows.size:
            continue
        values = data.data[valid]
        count += int(values.size)
        negative_count += int(np.count_nonzero(values < 0))
        zero_count += int(np.count_nonzero(values == 0))
        minimum = min(minimum, float(values.min()))
        maximum = max(maximum, float(values.max()))
        top = min(top, int(win.row_off) + int(rows.min()))
        bottom = max(bottom, int(win.row_off) + int(rows.max()))
        left = min(left, int(win.col_off) + int(cols.min()))
        right = max(right, int(win.col_off) + int(cols.max()))
    if not count:
        raise ValueError("No finite, unmasked cells found")
    crop = Window(left, top, right-left+1, bottom-top+1)
    total = src.width * src.height
    kept = int(crop.width * crop.height)
    print(f"Original: {src.width:,} columns x {src.height:,} rows")
    print(f"Finite unmasked cells: {count:,}; masked/nonfinite: {total-count:,} ({100*(total-count)/total:.2f}%)")
    print(f"Resistance minimum: {minimum:.10g}; maximum: {maximum:.10g}")
    print(f"Zero cells: {zero_count:,}; negative cells: {negative_count:,}")
    print(f"Unmasked NaN/infinity cells: {invalid_finite_count:,}")
    print(f"Removable margins: top={top}, bottom={src.height-bottom-1}, left={left}, right={src.width-right-1} cells")
    print(f"Proposed: {int(crop.width):,} columns x {int(crop.height):,} rows")
    print(f"Cells removed: {total-kept:,} ({100*(total-kept)/total:.2f}%)")
    print(f"Invalid cells remaining INSIDE proposed rectangle: {kept-count:,}")
    print("This count includes empty corners/coastal areas; it does not identify enclosed holes.")
    print(f"Float32 array saving: {(total-kept)*4/2**20:.2f} MiB")
    print(f"Boolean mask saving: {(total-kept)/2**20:.2f} MiB")
    print("Cropping cannot remove diagonal empty corners while retaining a rectangular grid.")
    if negative_count or invalid_finite_count:
        raise ValueError("Invalid resistance values detected; resolve them before exporting a crop")
    return crop


def write_crop(src, crop, target):
    if target.exists():
        # Recover a complete file left by the old verification assertion.
        # Existing files are reused only after every verification passes.
        verify_crop(src, crop, target)
        print(f"Existing crop fully verified and reused: {target}")
        return
    # Recover completed crops left by earlier verification failures. Never
    # promote a pending file without checking geometry, values and masks.
    for candidate in sorted(target.parent.glob(target.stem + ".pending-*.tif")):
        try:
            verify_crop(src, crop, candidate)
        except (OSError, ValueError, rasterio.errors.RasterioError) as exc:
            print(f"Pending crop retained but not reused: {candidate.name}: {exc}")
            continue
        if target.exists():
            raise FileExistsError(f"Target appeared during recovery: {target}")
        candidate.rename(target)
        # Rasterio may use a companion mask in older environments.
        for suffix in (".msk", ".aux.xml"):
            companion = Path(str(candidate) + suffix)
            if companion.exists():
                companion.rename(Path(str(target) + suffix))
        print(f"Recovered, verified and finalized existing crop: {target}")
        return
    pending = target.with_name(target.stem + f".pending-{uuid4().hex}.tif")
    profile = src.profile.copy()
    profile.update(width=int(crop.width), height=int(crop.height),
                   transform=src.window_transform(crop), tiled=True,
                   blockxsize=128, blockysize=128, compress="lzw", BIGTIFF="IF_SAFER")
    # Explicit internal mask preserves input validity independently of its sentinel.
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        with rasterio.open(pending, "w", **profile) as dst:
            for _, outwin in dst.block_windows(1):
                inwin = Window(crop.col_off + outwin.col_off,
                               crop.row_off + outwin.row_off,
                               outwin.width, outwin.height)
                dst.write(src.read(1, window=inwin), 1, window=outwin)
                dst.write_mask(src.read_masks(1, window=inwin), window=outwin)
            if src.descriptions[0]:
                dst.set_band_description(1, src.descriptions[0])
            if src.units[0]:
                dst.set_band_unit(1, src.units[0])
            dst.scales, dst.offsets = src.scales, src.offsets
            dst.update_tags(AREA_OR_POINT=src.tags().get("AREA_OR_POINT", "Area"))
    verify_crop(src, crop, pending)
    if target.exists():
        raise FileExistsError(f"Target appeared during writing; verified file retained at {pending}")
    pending.rename(target)
    print(f"Saved and verified: {target}")
    print(f"Disk size: source={Path(src.name).stat().st_size/2**20:.2f} MiB; crop={target.stat().st_size/2**20:.2f} MiB")


def main():
    failures = 0
    for path in RASTER_PATHS:
        print(f"\n{'='*72}\n{path}")
        try:
            with rasterio.open(path) as src:
                crop = inspect(src)
                if WRITE_CROPPED:
                    if crop.width == src.width and crop.height == src.height:
                        print("No removable margins; use the original file.")
                    else:
                        write_crop(src, crop, path.with_name(path.stem + "_cropped.tif"))
        except (OSError, ValueError, rasterio.errors.RasterioError) as exc:
            failures += 1
            print(f"ERROR: {exc}")
    if failures:
        raise SystemExit(f"{failures} raster(s) failed; see diagnostics above. Originals were not modified.")


if __name__ == "__main__":
    main()
