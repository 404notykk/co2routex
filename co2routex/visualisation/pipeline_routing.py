"""
pipeline_routing.py

Compare:
    1. Straight-line connection between two CO2RouteX nodes
    2. A* least-spatial-resistance route

Both are plotted over the same spatial resistance raster.
"""

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import Normalize
from pyproj import Transformer


# ============================================================
# USER SETTINGS
# ============================================================

# Spatial resistance raster
RASTER_PATH = Path(
    "../database/pipeline/spatial_cost_resistance/spatial_resistance_NL.tif"
)

# Routed CO2RouteX workbook
WORKBOOK_PATH = Path(
    "../routing_algorithm/astar_raster_router/benchmark_output/runs/run_08/node_metrics_routed.xlsx"
)

NODES_SHEET = "nodes"

# CRS used by longitude / latitude in workbook
NODES_CRS = "EPSG:4326"

# A* route output
ROUTE_PATH = Path(
    "../routing_algorithm/astar_raster_router/benchmark_output/runs/run_08/routes.gpkg"
)

# Set to None to read the default layer
ROUTE_LAYER = None


# ============================================================
# NODES TO DISPLAY
# ============================================================

# Can be:
#   1
#   "1"
#   "node1"
#
# Your current workbook contains:
#   node_id = 1, node_name = node1
#   node_id = 2, node_name = node2

FROM_NODE = 1
TO_NODE = 2


# ============================================================
# OUTPUT
# ============================================================

OUTPUT_FIGURE = Path(
    "pipeline_routing_comparison.png"
)

DPI = 300


# ============================================================
# HELPERS
# ============================================================

def clean_string(value):
    """
    Convert values to clean comparable strings.

    Useful because Excel may read node IDs as:
        1
        1.0
        "1"
    """

    if pd.isna(value):
        return ""

    # Convert integer-like floats such as 1.0 -> "1"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    return str(value).strip()


def read_nodes(workbook_path: Path) -> pd.DataFrame:
    """
    Read nodes from the CO2RouteX workbook.
    """

    if not workbook_path.exists():
        raise FileNotFoundError(
            f"Workbook not found:\n{workbook_path.resolve()}"
        )

    nodes = pd.read_excel(
        workbook_path,
        sheet_name=NODES_SHEET,
    )

    required = {
        "node_id",
        "longitude",
        "latitude",
    }

    missing = required - set(nodes.columns)

    if missing:
        raise ValueError(
            f"Missing required columns in '{NODES_SHEET}' sheet: "
            f"{sorted(missing)}"
        )

    # Create cleaned comparison fields
    nodes["_node_id_clean"] = nodes["node_id"].apply(clean_string)

    if "node_name" in nodes.columns:
        nodes["_node_name_clean"] = nodes["node_name"].apply(clean_string)
    else:
        nodes["_node_name_clean"] = ""

    return nodes


def get_node(
    nodes: pd.DataFrame,
    node_identifier,
) -> pd.Series:
    """
    Find a node using either node_id or node_name.

    Examples:
        get_node(nodes, 1)
        get_node(nodes, "1")
        get_node(nodes, "node1")
    """

    identifier = clean_string(node_identifier)

    # --------------------------------------------------------
    # First search node_id
    # --------------------------------------------------------

    by_id = nodes[
        nodes["_node_id_clean"] == identifier
    ]

    if not by_id.empty:
        return by_id.iloc[0]

    # --------------------------------------------------------
    # Then search node_name
    # --------------------------------------------------------

    by_name = nodes[
        nodes["_node_name_clean"] == identifier
    ]

    if not by_name.empty:
        return by_name.iloc[0]

    available_ids = nodes["_node_id_clean"].tolist()
    available_names = nodes["_node_name_clean"].tolist()

    raise ValueError(
        f"\nNode '{node_identifier}' not found.\n\n"
        f"Available node IDs:\n"
        f"{available_ids}\n\n"
        f"Available node names:\n"
        f"{available_names}\n"
    )


def get_actual_node_id(node: pd.Series) -> str:
    """
    Return actual node ID from selected workbook row.
    """

    return clean_string(node["node_id"])


def transform_node(
    node: pd.Series,
    target_crs,
):
    """
    Transform longitude/latitude coordinates from workbook CRS
    into raster CRS.
    """

    transformer = Transformer.from_crs(
        NODES_CRS,
        target_crs,
        always_xy=True,
    )

    longitude = float(node["longitude"])
    latitude = float(node["latitude"])

    x, y = transformer.transform(
        longitude,
        latitude,
    )

    return x, y


def node_label(node: pd.Series) -> str:
    """
    Use node_name as visual label when available.
    Otherwise use node_id.
    """

    if "node_name" in node.index:

        node_name = clean_string(
            node["node_name"]
        )

        if node_name:
            return node_name

    return get_actual_node_id(node)


def read_route(
    route_path: Path,
    raster_crs,
    actual_from_id: str,
    actual_to_id: str,
):
    """
    Read A* route output and select the requested connection.
    """

    if not route_path.exists():
        raise FileNotFoundError(
            f"Route file not found:\n{route_path.resolve()}"
        )

    suffix = route_path.suffix.lower()

    # --------------------------------------------------------
    # Read route file
    # --------------------------------------------------------

    if suffix == ".gpkg":

        if ROUTE_LAYER:

            routes = gpd.read_file(
                route_path,
                layer=ROUTE_LAYER,
            )

        else:

            routes = gpd.read_file(
                route_path
            )

    elif suffix in {
        ".geojson",
        ".json",
    }:

        routes = gpd.read_file(
            route_path
        )

    else:

        raise ValueError(
            "ROUTE_PATH must point to a "
            ".gpkg, .geojson or .json file."
        )

    if routes.empty:
        raise ValueError(
            f"No route geometries found in:\n"
            f"{route_path.resolve()}"
        )

    print("\nRoute file columns:")
    print(routes.columns.tolist())

    # --------------------------------------------------------
    # Select requested pair
    # --------------------------------------------------------

    if {
        "from_id",
        "to_id",
    }.issubset(routes.columns):

        route_from = routes["from_id"].apply(clean_string)
        route_to = routes["to_id"].apply(clean_string)

        print(
            f"\nSearching route: "
            f"{actual_from_id} -> {actual_to_id}"
        )

        # Normal direction
        mask = (
            (route_from == actual_from_id)
            &
            (route_to == actual_to_id)
        )

        selected = routes[
            mask
        ].copy()

        # ----------------------------------------------------
        # Try reverse direction if necessary
        # ----------------------------------------------------

        if selected.empty:

            reverse_mask = (
                (route_from == actual_to_id)
                &
                (route_to == actual_from_id)
            )

            selected = routes[
                reverse_mask
            ].copy()

            if not selected.empty:
                print(
                    "Route found in reverse direction."
                )

        if selected.empty:

            available_pairs = list(
                zip(
                    route_from.tolist(),
                    route_to.tolist(),
                )
            )

            raise ValueError(
                f"\nNo route found for "
                f"{actual_from_id} -> {actual_to_id}.\n\n"
                f"Available route pairs:\n"
                f"{available_pairs}"
            )

        routes = selected

    else:

        print(
            "\nWarning: route file does not contain "
            "'from_id' and 'to_id'.\n"
            "All route geometries will be plotted."
        )

    # --------------------------------------------------------
    # CRS handling
    # --------------------------------------------------------

    if routes.crs is None:
        raise ValueError(
            "Route file does not contain CRS information."
        )

    if routes.crs != raster_crs:

        routes = routes.to_crs(
            raster_crs
        )

    return routes


# ============================================================
# MAIN
# ============================================================

def main():

    # ========================================================
    # READ WORKBOOK
    # ========================================================

    nodes = read_nodes(
        WORKBOOK_PATH
    )

    from_node = get_node(
        nodes,
        FROM_NODE,
    )

    to_node = get_node(
        nodes,
        TO_NODE,
    )

    # --------------------------------------------------------
    # Resolve actual IDs
    # --------------------------------------------------------

    actual_from_id = get_actual_node_id(
        from_node
    )

    actual_to_id = get_actual_node_id(
        to_node
    )

    print("\n========================================")
    print("Selected nodes")
    print("========================================")

    print(
        f"FROM: ID={actual_from_id}, "
        f"name={node_label(from_node)}, "
        f"lon={from_node['longitude']}, "
        f"lat={from_node['latitude']}"
    )

    print(
        f"TO:   ID={actual_to_id}, "
        f"name={node_label(to_node)}, "
        f"lon={to_node['longitude']}, "
        f"lat={to_node['latitude']}"
    )

    # ========================================================
    # READ RASTER
    # ========================================================

    if not RASTER_PATH.exists():
        raise FileNotFoundError(
            f"Raster not found:\n"
            f"{RASTER_PATH.resolve()}"
        )

    with rasterio.open(
        RASTER_PATH
    ) as src:

        raster = src.read(
            1,
            masked=True,
        )

        raster_crs = src.crs
        bounds = src.bounds

    if raster_crs is None:
        raise ValueError(
            "Spatial resistance raster does not contain CRS information."
        )

    print("\nRaster CRS:")
    print(raster_crs)

    # ========================================================
    # TRANSFORM NODE COORDINATES
    # ========================================================

    from_x, from_y = transform_node(
        from_node,
        raster_crs,
    )

    to_x, to_y = transform_node(
        to_node,
        raster_crs,
    )

    print("\nTransformed node coordinates:")

    print(
        f"{node_label(from_node)}: "
        f"x={from_x:.2f}, y={from_y:.2f}"
    )

    print(
        f"{node_label(to_node)}: "
        f"x={to_x:.2f}, y={to_y:.2f}"
    )

    # ========================================================
    # READ A* ROUTE
    # ========================================================

    route = read_route(
        ROUTE_PATH,
        raster_crs,
        actual_from_id,
        actual_to_id,
    )

    # ========================================================
    # RASTER DISPLAY RANGE
    # ========================================================

    valid_values = raster.compressed()

    if valid_values.size == 0:
        raise ValueError(
            "Raster contains no valid values."
        )

    # Use percentiles only for visualisation.
    # Actual raster values remain unchanged.
    vmin = np.nanpercentile(
        valid_values,
        2,
    )

    vmax = np.nanpercentile(
        valid_values,
        98,
    )

    # Safety in case raster is almost constant
    if np.isclose(vmin, vmax):

        vmin = float(
            np.nanmin(valid_values)
        )

        vmax = float(
            np.nanmax(valid_values)
        )

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
        figsize=(16, 8),
        constrained_layout=True,
    )

    # ========================================================
    # COMMON RASTER + NODES
    # ========================================================

    for ax in axes:

        image = ax.imshow(
            raster,
            extent=raster_extent,
            origin="upper",
            cmap="viridis",
            norm=norm,
            interpolation="nearest",
        )

        # ----------------------------------------------------
        # From node
        # ----------------------------------------------------

        ax.scatter(
            from_x,
            from_y,
            s=100,
            marker="o",
            edgecolor="black",
            linewidth=1.2,
            zorder=6,
            label=node_label(from_node),
        )

        # ----------------------------------------------------
        # To node
        # ----------------------------------------------------

        ax.scatter(
            to_x,
            to_y,
            s=110,
            marker="s",
            edgecolor="black",
            linewidth=1.2,
            zorder=6,
            label=node_label(to_node),
        )

        # ----------------------------------------------------
        # Node labels
        # ----------------------------------------------------

        ax.annotate(
            node_label(from_node),
            (from_x, from_y),
            xytext=(8, 8),
            textcoords="offset points",
            fontsize=10,
            weight="bold",
            zorder=7,
        )

        ax.annotate(
            node_label(to_node),
            (to_x, to_y),
            xytext=(8, 8),
            textcoords="offset points",
            fontsize=10,
            weight="bold",
            zorder=7,
        )

        ax.set_aspect(
            "equal"
        )

        ax.set_xlabel(
            "Easting [m]"
        )

        ax.set_ylabel(
            "Northing [m]"
        )

    # ========================================================
    # LEFT PANEL:
    # STRAIGHT LINE
    # ========================================================

    axes[0].plot(
        [from_x, to_x],
        [from_y, to_y],
        color="white",
        linewidth=2.5,
        linestyle="--",
        zorder=5,
        label="Straight-line connection",
    )

    straight_distance_m = np.hypot(
        to_x - from_x,
        to_y - from_y,
    )

    axes[0].set_title(
        "Straight-line connection\n"
        f"Distance = "
        f"{straight_distance_m / 1000:.1f} km",
        fontsize=13,
        weight="bold",
    )

    # ========================================================
    # RIGHT PANEL:
    # A* ROUTE
    # ========================================================

    route.plot(
        ax=axes[1],
        color="white",
        linewidth=3,
        zorder=5,
    )

    route_length_m = (
        route.geometry.length.sum()
    )

    axes[1].set_title(
        "A* least-spatial-resistance route\n"
        f"Route length = "
        f"{route_length_m / 1000:.1f} km",
        fontsize=13,
        weight="bold",
    )

    # Dummy line purely for legend
    axes[1].plot(
        [],
        [],
        linewidth=3,
        label="A* least-resistance route",
    )

    # ========================================================
    # COMMON MAP EXTENT
    # ========================================================

    route_minx, route_miny, route_maxx, route_maxy = (
        route.total_bounds
    )

    minx = min(
        route_minx,
        from_x,
        to_x,
    )

    maxx = max(
        route_maxx,
        from_x,
        to_x,
    )

    miny = min(
        route_miny,
        from_y,
        to_y,
    )

    maxy = max(
        route_maxy,
        from_y,
        to_y,
    )

    width = maxx - minx
    height = maxy - miny

    padding = (
        max(
            width,
            height,
        )
        * 0.15
    )

    # Minimum 1 km padding
    padding = max(
        padding,
        1000,
    )

    for ax in axes:

        ax.set_xlim(
            minx - padding,
            maxx + padding,
        )

        ax.set_ylim(
            miny - padding,
            maxy + padding,
        )

    # ========================================================
    # LEGENDS
    # ========================================================

    axes[0].legend(
        loc="upper right",
        frameon=True,
    )

    axes[1].legend(
        loc="upper right",
        frameon=True,
    )

    # ========================================================
    # COLORBAR
    # ========================================================

    colorbar = fig.colorbar(
        image,
        ax=axes,
        shrink=0.78,
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
        f"CO2RouteX pipeline routing: "
        f"{node_label(from_node)} → "
        f"{node_label(to_node)}",
        fontsize=16,
        weight="bold",
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

    print("\n========================================")
    print("Results")
    print("========================================")

    print(
        f"Straight-line distance: "
        f"{straight_distance_m / 1000:.2f} km"
    )

    print(
        f"A* route distance: "
        f"{route_length_m / 1000:.2f} km"
    )

    print(
        f"Figure saved to:\n"
        f"{OUTPUT_FIGURE.resolve()}"
    )

    plt.show()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()