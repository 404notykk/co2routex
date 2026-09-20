# CO2RouteX: boundary and network preparation

## Inputs and outputs

All paths below are relative to the inner co2routex directory.

| Script | Input | Output |
|---|---|---|
| mask_nuts_countries.py | database/pipeline/raw/boundaries/NUTS_RG_01M_2024_3035.gpkg, or the same-named .shp within an extracted subfolder | database/pipeline/intermediate/boundaries/NL.shp, DE.shp, NO.shp |
| osm_subset.py | database/pipeline/raw/existing_network/netherlands-*.osm.pbf, germany-*.osm.pbf, norway-*.osm.pbf | database/pipeline/intermediate/existing_motorways/COUNTRY_motorways.gpkg; existing_railways/COUNTRY_railways.gpkg; existing_pipelines/COUNTRY_pipelines.gpkg |

Download the NUTS 2024 region polygons at 01M in EPSG:3035 from: https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units/territorial-units-statistics. Keep all shapefile
components together if using SHP. Only one matching GPKG or SHP should be
present; alternatively set INPUT_FILE at the top of the script. For a
multi-layer GPKG, set INPUT_LAYER to its region-polygon layer name. No automatic
choice between multiple layers or source versions is made.

Place one original country OSM PBF per country in raw/existing_network. Dated
and '-latest' filenames are accepted. Record download/snapshot dates separately
for reproducibility. If keeping several snapshots, select exact filenames in
INPUT_FILES. Change COUNTRIES in both scripts to process a subset; adding a
new OSM country also requires its COUNTRY_NAMES entry.

## Requirements and running

Boundary extraction requires GeoPandas 1.0 or newer and its vector I/O engine.
OSM extraction requires the osmium-tool and GDAL ogr2ogr command-line programs
on PATH. No ArcGIS installation or license is used by either script.

Run mask_nuts_countries.py, then osm_subset.py. They are independent preparation
steps. From their folder you can check file discovery without GIS dependencies:

```bash
python mask_nuts_countries.py --dry-run
python osm_subset.py --dry-run
```

Dry runs verify input paths and print output destinations; they do not validate
file contents. Normal execution replaces the named generated outputs. Close
those outputs in GIS applications before rerunning. OSM outputs are staged so
a failed conversion does not replace an existing final GPKG.

## Preserved extraction behaviour

NUTS selection uses LEVL_CODE=0 and CNTR_CODE for NL, DE, NO. Country outputs
are projected to EPSG:3035 and remain shapefiles to match the existing layout.
The OSM filters are exactly the original ones:

- motorways: w/highway=motorway
- railways: w/railway=rail
- pipelines: w/man_made=pipeline

The output GPKG layer remains 'lines'. OSM extraction retains the source CRS;
projection, clipping, 10 m rasterisation, multiplier assignment and 100 m mean
aggregation belong to subsequent processing. Country extracts can contain
cross-border features; this step does not country-clip them. Pipeline selection
does not filter by transported substance or planned/operational status beyond
the original man_made=pipeline tag.

## Validation of this update

Syntax and isolated path-discovery checks were run, including from a different
working directory and with duplicate source files. Full GIS execution was not
run: source datasets and GIS dependencies were unavailable in the editing
runtime. The remaining ArcGIS-to-Python raster stages are not included in this
update.
