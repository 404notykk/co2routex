from __future__ import annotations

from pathlib import Path

import yaml

from .models import NetworkSource, Settings


def _resolve(base: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def load_settings(config_path: str | Path) -> Settings:
    config_path = Path(config_path).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    base = config_path.parent
    if "workbook_path" not in raw:
        raise ValueError("Missing required configuration key: workbook_path")
    if not raw.get("networks"):
        raise ValueError("At least one country must be configured under networks")

    workbook_path = _resolve(base, raw["workbook_path"])
    output_workbook_raw = raw.get("output_workbook_path")
    output_workbook_path = (
        _resolve(base, output_workbook_raw)
        if output_workbook_raw
        else workbook_path
    )

    networks: dict[str, NetworkSource] = {}
    for country, item in raw["networks"].items():
        if isinstance(item, str):
            item = {"path": item}
        if "path" not in item:
            raise ValueError(f"Network {country!r} is missing a path")
        networks[str(country).strip().upper()] = NetworkSource(
            path=_resolve(base, item["path"]),
            layer=str(item.get("layer", "truck_network")),
        )

    routing = raw.get("routing", {})
    rules = raw.get("connection_rules", {})
    outputs = raw.get("outputs", {})

    connection_policy = str(rules.get("policy", "directed_chain")).lower()
    if connection_policy not in {"baseline", "directed_chain"}:
        raise ValueError(
            "connection_rules.policy must be 'baseline' or 'directed_chain'"
        )

    candidate_source = str(rules.get("candidate_source", "generated")).lower()
    if candidate_source not in {"generated", "existing_nonzero"}:
        raise ValueError(
            "connection_rules.candidate_source must be 'generated' or "
            "'existing_nonzero'"
        )

    maximum_snap_distance_m = float(
        routing.get("maximum_snap_distance_m", 10_000.0)
    )
    if maximum_snap_distance_m < 0:
        raise ValueError("maximum_snap_distance_m must be non-negative")

    return Settings(
        config_path=config_path,
        workbook_path=workbook_path,
        output_workbook_path=output_workbook_path,
        nodes_sheet=str(raw.get("nodes_sheet", "nodes")),
        truck_sheet=str(raw.get("truck_sheet", "truck")),
        nodes_crs=str(raw.get("nodes_crs", "EPSG:4326")),
        working_crs=str(raw.get("working_crs", "EPSG:3035")),
        output_crs=str(raw.get("output_crs", "EPSG:4326")),
        networks=networks,
        maximum_snap_distance_m=maximum_snap_distance_m,
        include_snap_distance_in_metric=bool(
            routing.get("include_snap_distance_in_metric", True)
        ),
        respect_oneway=bool(routing.get("respect_oneway", True)),
        connection_policy=connection_policy,
        candidate_source=candidate_source,
        output_dir=_resolve(base, outputs.get("directory", "output")),
        routes_filename=str(outputs.get("routes_filename", "truck_routes.gpkg")),
        summary_filename=str(
            outputs.get("summary_filename", "truck_route_summary.csv")
        ),
    )
