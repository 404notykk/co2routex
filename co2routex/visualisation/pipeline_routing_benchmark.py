"""
Visualize all CO2RouteX benchmark routes.

Left:
    Straight-line connections

Right:
    A* least-spatial-resistance routes

Both panels use the same spatial-resistance raster and route colours.
"""

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
from pyproj import Transformer


# ============================================================
# USER SETTINGS
# ============================================================

RASTER_PATH = Path(
    "../database/pipeline/spatial_cost_resistance/"
    "spatial_resistance_NL.tif"
)

BENCHMARK_DIRECTORY = Path(
    "../routing_algorithm/astar_raster_router/benchmark_output"
)

# Any measured run can be used because the routes are identical.
RUN_DIRECTORY = BENCHMARK_DIRECTORY / "runs" / "run_08"

WORKBOOK_PATH = (
    RUN_DIRECTORY / "node_metrics_routed.xlsx"
)

ROUTE_PATH = (
    RUN_DIRECTORY / "routes.gpkg"
)

SUMMARY_PATH = (
    BENCHMARK_DIRECTORY / "benchmark_summary.csv"
)

NODES_SHEET = "nodes"
NODES_CRS = "EPSG:4326"

# Set to None to read the default GeoPackage layer.
ROUTE_LAYER = None

OUTPUT_FIGURE = Path(
    "benchmark_routes_comparison.png"
)

DPI = 300


# ============================================================
# HELPERS
# ============================================================

def clean_string(value) -> str:
    """Convert Excel and GeoPackage identifiers to comparable strings."""
    if pd.isna(value):
        return ""

    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    return str(value).strip()


def read_nodes(workbook_path: Path) -> pd.DataFrame:
    """Read benchmark nodes from the routed workbook."""
    if not workbook_path.exists():
        raise FileNotFoundError(
            f"Workbook not found:\n{workbook_path.resolve()}"
        )

    nodes = pd.read_excel(
        workbook_path,
        sheet_name=NODES_SHEET,
    )

    required_columns = {
        "node_id",
        "longitude",
        "latitude",
    }

    missing_columns = required_columns - set(nodes.columns)

    if missing_columns:
        raise ValueError(
            f"Missing columns in '{NODES_SHEET}': "
            f"{sorted(missing_columns)}"
        )

    nodes["_node_id"] = nodes["node_id"].apply(clean_string)

    if "node_name" not in nodes.columns:
        nodes["node_name"] = nodes["_node_id"]

    if "node_type" not in nodes.columns:
        nodes["node_type"] = ""

    nodes["_node_name"] = nodes["node_name"].apply(clean_string)
    nodes["_node_type"] = (
        nodes["node_type"]
        .apply(clean_string)
        .str.lower()
    )

    return nodes


def transform_nodes(
    nodes: pd.DataFrame,
    target_crs,
) -> pd.DataFrame:
    """Transform node longitude and latitude to the raster CRS."""
    transformer = Transformer.from_crs(
        NODES_CRS,
        target_crs,
        always_xy=True,
    )

    transformed = nodes.copy()

    coordinates = [
        transformer.transform(
            float(longitude),
            float(latitude),
        )
        for longitude, latitude in zip(
            transformed["longitude"],
            transformed["latitude"],
        )
    ]

    transformed["x"] = [
        coordinate[0]
        for coordinate in coordinates
    ]

    transformed["y"] = [
        coordinate[1]
        for coordinate in coordinates
    ]

    return transformed


def read_routes(
    route_path: Path,
    raster_crs,
) -> gpd.GeoDataFrame:
    """Read every successful benchmark route."""
    if not route_path.exists():
        raise FileNotFoundError(
            f"Route file not found:\n{route_path.resolve()}"
        )

    if ROUTE_LAYER:
        routes = gpd.read_file(
            route_path,
            layer=ROUTE_LAYER,
        )
    else:
        routes = gpd.read_file(route_path)

    if routes.empty:
        raise ValueError(
            f"No routes found in:\n{route_path.resolve()}"
        )

    required_columns = {
        "from_id",
        "to_id",
    }

    missing_columns = required_columns - set(routes.columns)

    if missing_columns:
        raise ValueError(
            "Route file is missing columns: "
            f"{sorted(missing_columns)}"
        )

    if routes.crs is None:
        raise ValueError(
            "Route file does not contain CRS information."
        )

    if routes.crs != raster_crs:
        routes = routes.to_crs(raster_crs)

    routes["_from_id"] = routes["from_id"].apply(clean_string)
    routes["_to_id"] = routes["to_id"].apply(clean_string)

    if "status" in routes.columns:
        routes = routes[
            routes["status"].astype(str).str.lower() == "ok"
        ].copy()

    return routes


def read_summary(summary_path: Path) -> pd.DataFrame:
    """Read the combined benchmark summary."""
    if not summary_path.exists():
        raise FileNotFoundError(
            f"Benchmark summary not found:\n"
            f"{summary_path.resolve()}"
        )

    summary = pd.read_csv(summary_path)

    required_columns = {
        "from_id",
        "to_id",
        "target_distance_km",
        "straight_line_distance_km",
        "routed_distance_km",
        "astar_time_median_s",
    }

    missing_columns = required_columns - set(summary.columns)

    if missing_columns:
        raise ValueError(
            "Benchmark summary is missing columns: "
            f"{sorted(missing_columns)}"
        )

    summary["_from_id"] = summary["from_id"].apply(clean_string)
    summary["_to_id"] = summary["to_id"].apply(clean_string)

    return summary


def node_label(node: pd.Series) -> str:
    """Return a useful node label."""
    name = clean_string(node.get("node_name"))

    if name:
        return name

    return clean_string(node["node_id"])


def metric_text(value, decimals=1) -> str:
    """Format a metric value while handling missing data."""
    if value is None or pd.isna(value):
        return "n/a"

    return f"{float(value):.{decimals}f}"


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    # ========================================================
    # READ RASTER
    # ========================================================

    if not RASTER_PATH.exists():
        raise FileNotFoundError(
            f"Raster not found:\n{RASTER_PATH.resolve()}"
        )

    with rasterio.open(RASTER_PATH) as source:
        raster = source.read(
            1,
            masked=True,
        )

        raster_crs = source.crs
        bounds = source.bounds

    if raster_crs is None:
        raise ValueError(
            "Spatial resistance raster has no CRS."
        )

    print("\nRaster CRS:")
    print(raster_crs)

    # ========================================================
    # READ NODES, ROUTES AND SUMMARY
    # ========================================================

    nodes = read_nodes(WORKBOOK_PATH)

    nodes = transform_nodes(
        nodes,
        raster_crs,
    )

    routes = read_routes(
        ROUTE_PATH,
        raster_crs,
    )

    summary = read_summary(
        SUMMARY_PATH
    )

    # Add benchmark information to each route.
    routes = routes.merge(
        summary[
            [
                "_from_id",
                "_to_id",
                "target_distance_km",
                "straight_line_distance_km",
                "routed_distance_km",
                "astar_time_median_s",
                "explored_cells",
                "path_cells",
            ]
        ],
        on=[
            "_from_id",
            "_to_id",
        ],
        how="left",
    )

    routes = routes.sort_values(
        "target_distance_km",
        na_position="last",
    ).reset_index(drop=True)

    nodes_by_id = {
        row["_node_id"]: row
        for _, row in nodes.iterrows()
    }

    # Confirm that all route endpoints exist in the workbook.
    for _, route in routes.iterrows():
        if route["_from_id"] not in nodes_by_id:
            raise ValueError(
                f"Origin node {route['_from_id']} "
                "is missing from the workbook."
            )

        if route["_to_id"] not in nodes_by_id:
            raise ValueError(
                f"Destination node {route['_to_id']} "
                "is missing from the workbook."
            )

    # ========================================================
    # RASTER DISPLAY RANGE
    # ========================================================

    valid_values = raster.compressed()

    if valid_values.size == 0:
        raise ValueError(
            "Raster contains no valid values."
        )

    vmin = np.nanpercentile(
        valid_values,
        2,
    )

    vmax = np.nanpercentile(
        valid_values,
        98,
    )

    if np.isclose(vmin, vmax):
        vmin = float(np.nanmin(valid_values))
        vmax = float(np.nanmax(valid_values))

        if np.isclose(vmin, vmax):
            vmax = vmin + 1

    norm = Normalize(
        vmin=vmin,
        vmax=vmax,
    )

    raster_extent = [
        bounds.left,
        bounds.right,
        bounds.bottom,
        bounds.top,
    ]

    # ========================================================
    # CREATE FIGURE
    # ========================================================

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(18, 9),
        constrained_layout=True,
    )

    colours = plt.get_cmap(
        "tab10"
    )(
        np.linspace(
            0,
            1,
            max(len(routes), 1),
        )
    )

    # ========================================================
    # BACKGROUND RASTER
    # ========================================================

    for axis in axes:
        image = axis.imshow(
            raster,
            extent=raster_extent,
            origin="upper",
            cmap="Greys",
            norm=norm,
            interpolation="nearest",
            alpha=0.92,
        )

        axis.set_aspect("equal")

        axis.set_xlabel(
            "Easting [m]"
        )

        axis.set_ylabel(
            "Northing [m]"
        )

        axis.grid(
            alpha=0.15,
            linewidth=0.5,
        )

    # ========================================================
    # DRAW ROUTES
    # ========================================================

    straight_legend = []
    route_legend = []

    for route_number, route in routes.iterrows():
        colour = colours[route_number]

        from_node = nodes_by_id[
            route["_from_id"]
        ]

        to_node = nodes_by_id[
            route["_to_id"]
        ]

        from_x = float(from_node["x"])
        from_y = float(from_node["y"])
        to_x = float(to_node["x"])
        to_y = float(to_node["y"])

        target_distance = route.get(
            "target_distance_km"
        )

        straight_distance = route.get(
            "straight_line_distance_km"
        )

        routed_distance = route.get(
            "routed_distance_km"
        )

        astar_time = route.get(
            "astar_time_median_s"
        )

        # ----------------------------------------------------
        # Straight-line panel
        # ----------------------------------------------------

        axes[0].plot(
            [from_x, to_x],
            [from_y, to_y],
            color=colour,
            linewidth=2.5,
            linestyle="--",
            zorder=4,
        )

        straight_label = (
            f"{metric_text(target_distance, 0)} km case: "
            f"{metric_text(straight_distance, 1)} km"
        )

        straight_legend.append(
            Line2D(
                [0],
                [0],
                color=colour,
                linewidth=2.5,
                linestyle="--",
                label=straight_label,
            )
        )

        # ----------------------------------------------------
        # A* route panel
        # ----------------------------------------------------

        route_geometry = gpd.GeoSeries(
            [route.geometry],
            crs=routes.crs,
        )

        route_geometry.plot(
            ax=axes[1],
            color=colour,
            linewidth=3,
            zorder=4,
        )

        route_label = (
            f"{metric_text(target_distance, 0)} km case: "
            f"route {metric_text(routed_distance, 1)} km; "
            f"A* {metric_text(astar_time, 2)} s"
        )

        route_legend.append(
            Line2D(
                [0],
                [0],
                color=colour,
                linewidth=3,
                label=route_label,
            )
        )

        # ----------------------------------------------------
        # Plot origin emitter on both panels
        # ----------------------------------------------------

        for axis in axes:
            axis.scatter(
                from_x,
                from_y,
                s=85,
                marker="o",
                color=colour,
                edgecolor="black",
                linewidth=1,
                zorder=6,
            )

            axis.annotate(
                node_label(from_node),
                (from_x, from_y),
                xytext=(7, 7),
                textcoords="offset points",
                fontsize=9,
                weight="bold",
                color="black",
                zorder=7,
            )

    # ========================================================
    # PLOT DESTINATION NODES
    # ========================================================

    destination_ids = routes[
        "_to_id"
    ].drop_duplicates()

    for destination_id in destination_ids:
        destination_node = nodes_by_id[
            destination_id
        ]

        destination_x = float(
            destination_node["x"]
        )

        destination_y = float(
            destination_node["y"]
        )

        for axis in axes:
            axis.scatter(
                destination_x,
                destination_y,
                s=190,
                marker="*",
                color="gold",
                edgecolor="black",
                linewidth=1.3,
                zorder=8,
            )

            axis.annotate(
                f"{node_label(destination_node)} (storage)",
                (
                    destination_x,
                    destination_y,
                ),
                xytext=(8, -16),
                textcoords="offset points",
                fontsize=10,
                weight="bold",
                color="black",
                zorder=9,
            )

    # ========================================================
    # COMMON MAP EXTENT
    # ========================================================

    route_minx, route_miny, route_maxx, route_maxy = (
        routes.total_bounds
    )

    node_minx = nodes["x"].min()
    node_maxx = nodes["x"].max()
    node_miny = nodes["y"].min()
    node_maxy = nodes["y"].max()

    minimum_x = min(
        route_minx,
        node_minx,
    )

    maximum_x = max(
        route_maxx,
        node_maxx,
    )

    minimum_y = min(
        route_miny,
        node_miny,
    )

    maximum_y = max(
        route_maxy,
        node_maxy,
    )

    width = maximum_x - minimum_x
    height = maximum_y - minimum_y

    padding = max(
        max(width, height) * 0.08,
        5000,
    )

    for axis in axes:
        axis.set_xlim(
            minimum_x - padding,
            maximum_x + padding,
        )

        axis.set_ylim(
            minimum_y - padding,
            maximum_y + padding,
        )

    # ========================================================
    # TITLES AND LEGENDS
    # ========================================================

    axes[0].set_title(
        "Straight-line benchmark connections",
        fontsize=14,
        weight="bold",
    )

    axes[1].set_title(
        "A* least-spatial-resistance routes",
        fontsize=14,
        weight="bold",
    )

    storage_handle = Line2D(
        [0],
        [0],
        marker="*",
        markersize=14,
        markerfacecolor="gold",
        markeredgecolor="black",
        linestyle="None",
        label="Storage node",
    )

    axes[0].legend(
        handles=[
            *straight_legend,
            storage_handle,
        ],
        loc="best",
        fontsize=9,
        frameon=True,
    )

    axes[1].legend(
        handles=[
            *route_legend,
            storage_handle,
        ],
        loc="best",
        fontsize=9,
        frameon=True,
    )

    # ========================================================
    # COLORBAR
    # ========================================================

    colorbar = fig.colorbar(
        image,
        ax=axes,
        shrink=0.80,
        pad=0.02,
    )

    colorbar.set_label(
        "Spatial resistance multiplier",
        fontsize=11,
    )

    # ========================================================
    # OVERALL TITLE
    # ========================================================

    fig.suptitle(
        "CO2RouteX distance benchmark: "
        "straight-line connections and A* routes",
        fontsize=17,
        weight="bold",
    )

    # ========================================================
    # PRINT ROUTE SUMMARY
    # ========================================================

    print("\n========================================")
    print("Benchmark routes")
    print("========================================")

    for _, route in routes.iterrows():
        print(
            f"{route['_from_id']} -> {route['_to_id']}: "
            f"straight = "
            f"{metric_text(route['straight_line_distance_km'], 2)} km, "
            f"routed = "
            f"{metric_text(route['routed_distance_km'], 2)} km, "
            f"median A* = "
            f"{metric_text(route['astar_time_median_s'], 3)} s"
        )

    # ========================================================
    # SAVE
    # ========================================================

    OUTPUT_FIGURE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fig.savefig(
        OUTPUT_FIGURE,
        dpi=DPI,
        bbox_inches="tight",
    )

    print(
        f"\nFigure saved to:\n"
        f"{OUTPUT_FIGURE.resolve()}"
    )

    plt.show()


if __name__ == "__main__":
    main()