"""
truck_routing.py

Visualise a CO2RouteX truck route for presentation/slides.

Shows:
    - underlying truck road network
    - routed truck path
    - origin and destination nodes
    - route distance

Expected inputs:
    1. node_metrics_routed.xlsx
    2. truck network GeoPackage
    3. truck router output routes.gpkg

The workbook nodes, road network, and route may use geographic CRS data.
Everything is reprojected automatically to a metric plotting CRS.
"""

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from pyproj import Transformer


# ============================================================
# USER SETTINGS
# ============================================================

# ------------------------------------------------------------
# Workbook
# ------------------------------------------------------------

WORKBOOK_PATH = Path(
    "../routing_algorithm/astar_raster_router/examples/node_metrics_test.xlsx"
)

NODES_SHEET = "nodes"
NODES_CRS = "EPSG:4326"

# Metric CRS used for clipping, plotting, padding, and fallback length
# calculations. EPSG:3035 is consistent with the CO2RouteX European GIS data.
MAP_CRS = "EPSG:3035"


# ------------------------------------------------------------
# Truck road network
#
# Change this path if your actual NL network filename differs.
# ------------------------------------------------------------

ROAD_NETWORK_PATH = Path(
    "../database/container_based_truck/NL_road_network.gpkg"
)

ROAD_NETWORK_LAYER = "road_network"


# ------------------------------------------------------------
# Truck route output
# ------------------------------------------------------------

ROUTE_PATH = Path(
    "../routing_algorithm/truck_network_router/output/truck_routes.gpkg"
)

# Set to None if only one layer exists
ROUTE_LAYER = None


# ------------------------------------------------------------
# Nodes to visualise
#
# Can be ID:
#     1
#     2
#
# or node name:
#     "node1"
#     "node2"
# ------------------------------------------------------------

FROM_NODE = 1
TO_NODE = 2


# ------------------------------------------------------------
# Output
# ------------------------------------------------------------

OUTPUT_FIGURE = Path(
    "truck_routing_visualisation.png"
)

DPI = 300


# ------------------------------------------------------------
# Map padding
#
# 0.15 = 15% around route extent
# ------------------------------------------------------------

MAP_PADDING = 0.15


# ============================================================
# HELPERS
# ============================================================

def clean_string(value):
    """
    Convert Excel / GIS identifiers to comparable strings.

    Handles:
        1
        1.0
        "1"
    consistently.
    """

    if pd.isna(value):
        return ""

    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    return str(value).strip()


def read_nodes(workbook_path: Path) -> pd.DataFrame:
    """Read nodes from the workbook."""

    if not workbook_path.exists():
        raise FileNotFoundError(
            f"Workbook not found:\n"
            f"{workbook_path.resolve()}"
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
            f"Missing columns in '{NODES_SHEET}' sheet: "
            f"{sorted(missing)}"
        )

    nodes["_node_id_clean"] = (
        nodes["node_id"]
        .apply(clean_string)
    )

    if "node_name" in nodes.columns:

        nodes["_node_name_clean"] = (
            nodes["node_name"]
            .apply(clean_string)
        )

    else:

        nodes["_node_name_clean"] = ""

    return nodes


def get_node(
    nodes: pd.DataFrame,
    node_identifier,
) -> pd.Series:
    """
    Find node using either node_id or node_name.
    """

    identifier = clean_string(
        node_identifier
    )

    # Search node ID
    match = nodes[
        nodes["_node_id_clean"] == identifier
    ]

    if not match.empty:
        return match.iloc[0]

    # Search node name
    match = nodes[
        nodes["_node_name_clean"] == identifier
    ]

    if not match.empty:
        return match.iloc[0]

    raise ValueError(
        f"\nNode '{node_identifier}' not found.\n"
        f"Available IDs: "
        f"{nodes['_node_id_clean'].tolist()}\n"
        f"Available names: "
        f"{nodes['_node_name_clean'].tolist()}"
    )


def actual_node_id(node: pd.Series) -> str:
    """Return actual workbook node ID."""

    return clean_string(
        node["node_id"]
    )


def node_label(node: pd.Series) -> str:
    """Use node_name as plotting label."""

    if "node_name" in node.index:

        name = clean_string(
            node["node_name"]
        )

        if name:
            return name

    return actual_node_id(node)


def transform_node(
    node: pd.Series,
    target_crs,
):
    """
    Transform workbook longitude/latitude
    into target map CRS.
    """

    transformer = Transformer.from_crs(
        NODES_CRS,
        target_crs,
        always_xy=True,
    )

    x, y = transformer.transform(
        float(node["longitude"]),
        float(node["latitude"]),
    )

    return x, y


# ============================================================
# READ ROAD NETWORK
# ============================================================

def read_road_network():
    """Read the truck road network."""

    if not ROAD_NETWORK_PATH.exists():

        raise FileNotFoundError(
            f"Road network not found:\n"
            f"{ROAD_NETWORK_PATH.resolve()}"
        )

    try:

        road_network = gpd.read_file(
            ROAD_NETWORK_PATH,
            layer=ROAD_NETWORK_LAYER,
        )

    except Exception:

        print(
            "\nCould not read specified road-network layer "
            f"'{ROAD_NETWORK_LAYER}'."
        )

        print(
            "Trying default GeoPackage layer instead..."
        )

        road_network = gpd.read_file(
            ROAD_NETWORK_PATH
        )

    if road_network.empty:

        raise ValueError(
            "Truck road network contains no geometries."
        )

    if road_network.crs is None:

        raise ValueError(
            "Truck road network has no CRS."
        )

    return road_network


# ============================================================
# READ ROUTE
# ============================================================

def read_route(
    route_path: Path,
    target_crs,
    from_id: str,
    to_id: str,
):
    """Read and select requested truck route."""

    if not route_path.exists():

        raise FileNotFoundError(
            f"Truck route file not found:\n"
            f"{route_path.resolve()}"
        )

    suffix = route_path.suffix.lower()

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
            "ROUTE_PATH must be .gpkg, .geojson or .json"
        )

    if routes.empty:

        raise ValueError(
            "No truck routes found."
        )

    print("\nRoute columns:")
    print(routes.columns.tolist())

    # --------------------------------------------------------
    # Identify node pair columns
    # --------------------------------------------------------

    possible_pairs = [
        ("from_id", "to_id"),
        ("source_id", "target_id"),
        ("origin_id", "destination_id"),
    ]

    pair_columns = None

    for candidate in possible_pairs:

        if all(
            column in routes.columns
            for column in candidate
        ):
            pair_columns = candidate
            break

    # --------------------------------------------------------
    # Select requested route
    # --------------------------------------------------------

    if pair_columns is not None:

        from_column, to_column = pair_columns

        route_from = routes[
            from_column
        ].apply(clean_string)

        route_to = routes[
            to_column
        ].apply(clean_string)

        mask = (
            (route_from == from_id)
            &
            (route_to == to_id)
        )

        selected = routes[
            mask
        ].copy()

        if selected.empty:

            available_pairs = list(
                zip(
                    route_from.tolist(),
                    route_to.tolist(),
                )
            )

            raise ValueError(
                f"\nNo truck route found for "
                f"{from_id} -> {to_id}.\n"
                f"Available route pairs:\n"
                f"{available_pairs}"
            )

        routes = selected

    else:

        print(
            "\nWarning: node-pair columns were not found "
            "in route file."
        )

        print(
            "All route geometries will be displayed."
        )

    # --------------------------------------------------------
    # Reproject route if necessary
    # --------------------------------------------------------

    if routes.crs is None:

        raise ValueError(
            "Truck route file contains no CRS."
        )

    if routes.crs != target_crs:

        routes = routes.to_crs(
            target_crs
        )

    return routes


# ============================================================
# MAIN
# ============================================================

def main():

    # ========================================================
    # READ NODES
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

    from_id = actual_node_id(
        from_node
    )

    to_id = actual_node_id(
        to_node
    )

    print("\n========================================")
    print("Selected truck connection")
    print("========================================")

    print(
        f"FROM: {from_id} "
        f"({node_label(from_node)})"
    )

    print(
        f"TO:   {to_id} "
        f"({node_label(to_node)})"
    )

    # ========================================================
    # READ ROAD NETWORK
    # ========================================================

    roads = read_road_network()

    print("\nOriginal road network CRS:")
    print(roads.crs)

    # Reproject before clipping, plotting, padding, or measuring. If the
    # source is EPSG:4326, its coordinate units are degrees rather than metres.
    roads = roads.to_crs(MAP_CRS)
    map_crs = roads.crs

    print("\nVisualisation CRS:")
    print(map_crs)

    # ========================================================
    # READ ROUTE
    # ========================================================

    route = read_route(
        ROUTE_PATH,
        map_crs,
        from_id,
        to_id,
    )

    # ========================================================
    # TRANSFORM NODE COORDINATES
    # ========================================================

    from_x, from_y = transform_node(
        from_node,
        map_crs,
    )

    to_x, to_y = transform_node(
        to_node,
        map_crs,
    )

    # ========================================================
    # ROUTE EXTENT
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

    padding = max(
        width,
        height,
    ) * MAP_PADDING

    # At least 1 km padding
    padding = max(
        padding,
        1000,
    )

    plot_minx = minx - padding
    plot_maxx = maxx + padding
    plot_miny = miny - padding
    plot_maxy = maxy + padding

    # ========================================================
    # CLIP ROAD NETWORK TO MAP AREA
    #
    # Important for performance if NL network is large.
    # ========================================================

    roads_plot = roads.cx[
        plot_minx:plot_maxx,
        plot_miny:plot_maxy
    ]

    print(
        f"\nRoad segments displayed: "
        f"{len(roads_plot)}"
    )

    # ========================================================
    # ROUTE DISTANCE
    # ========================================================

    # Prefer the distance calculated by the truck router. metric_km matches
    # the value written to the truck worksheet when snap distances are enabled.
    if (
        "metric_km" in route.columns
        and route["metric_km"].notna().any()
    ):
        route_distance_km = float(
            route.loc[route["metric_km"].notna(), "metric_km"].iloc[0]
        )
        distance_source = "metric_km from truck router"

    elif (
        "network_km" in route.columns
        and route["network_km"].notna().any()
    ):
        route_distance_km = float(
            route.loc[route["network_km"].notna(), "network_km"].iloc[0]
        )
        distance_source = "network_km from truck router"

    else:
        # Safe fallback because route is now in metric EPSG:3035.
        route_distance_km = float(
            route.geometry.length.sum() / 1000.0
        )
        distance_source = "projected route geometry"

    # ========================================================
    # CREATE FIGURE
    # ========================================================

    fig, ax = plt.subplots(
        figsize=(12, 8)
    )

    # ========================================================
    # ROAD NETWORK
    # ========================================================

    roads_plot.plot(
        ax=ax,
        color="lightgrey",
        linewidth=0.7,
        alpha=0.8,
        zorder=1,
    )

    # ========================================================
    # ROUTE
    #
    # Draw a white outline beneath red route.
    # This makes the route extremely clear on slides.
    # ========================================================

    route.plot(
        ax=ax,
        color="white",
        linewidth=7,
        zorder=4,
    )

    route.plot(
        ax=ax,
        color="red",
        linewidth=4,
        zorder=5,
    )

    # ========================================================
    # NODES
    # ========================================================

    # Origin
    ax.scatter(
        from_x,
        from_y,
        s=170,
        marker="o",
        facecolor="deepskyblue",
        edgecolor="white",
        linewidth=2,
        zorder=7,
    )

    # Destination
    ax.scatter(
        to_x,
        to_y,
        s=180,
        marker="s",
        facecolor="orange",
        edgecolor="white",
        linewidth=2,
        zorder=7,
    )

    # ========================================================
    # NODE LABELS
    # ========================================================

    ax.annotate(
        node_label(from_node),
        (from_x, from_y),
        xytext=(10, 10),
        textcoords="offset points",
        fontsize=12,
        fontweight="bold",
        zorder=8,
    )

    ax.annotate(
        node_label(to_node),
        (to_x, to_y),
        xytext=(10, 10),
        textcoords="offset points",
        fontsize=12,
        fontweight="bold",
        zorder=8,
    )

    # ========================================================
    # MAP EXTENT
    # ========================================================

    ax.set_xlim(
        plot_minx,
        plot_maxx,
    )

    ax.set_ylim(
        plot_miny,
        plot_maxy,
    )

    ax.set_aspect(
        "equal"
    )

    # ========================================================
    # TITLE
    # ========================================================

    ax.set_title(
        f"Truck routing: "
        f"{node_label(from_node)} → {node_label(to_node)}\n"
        f"Truck route distance = {route_distance_km:.1f} km",
        fontsize=16,
        fontweight="bold",
        pad=14,
    )

    # ========================================================
    # LEGEND
    # ========================================================

    legend_elements = [

        Line2D(
            [0],
            [0],
            color="lightgrey",
            linewidth=2,
            label="Road network",
        ),

        Line2D(
            [0],
            [0],
            color="red",
            linewidth=4,
            label="Shortest road-network route",
        ),

        Line2D(
            [0],
            [0],
            marker="o",
            color="none",
            markerfacecolor="deepskyblue",
            markeredgecolor="white",
            markersize=10,
            label=node_label(from_node),
        ),

        Line2D(
            [0],
            [0],
            marker="s",
            color="none",
            markerfacecolor="orange",
            markeredgecolor="white",
            markersize=10,
            label=node_label(to_node),
        ),
    ]

    ax.legend(
        handles=legend_elements,
        loc="upper right",
        fontsize=10,
        frameon=True,
    )

    # ========================================================
    # CLEAN SLIDE APPEARANCE
    # ========================================================

    ax.set_xlabel(
        "Easting [m]",
        fontsize=11,
    )

    ax.set_ylabel(
        "Northing [m]",
        fontsize=11,
    )

    ax.grid(
        False
    )

    # ========================================================
    # SAVE
    # ========================================================

    OUTPUT_FIGURE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    plt.tight_layout()

    fig.savefig(
        OUTPUT_FIGURE,
        dpi=DPI,
        bbox_inches="tight",
    )

    print("\n========================================")
    print("Truck routing visualisation")
    print("========================================")

    print(
        f"Route distance: "
        f"{route_distance_km:.2f} km"
    )

    print(
        f"Distance source: "
        f"{distance_source}"
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
