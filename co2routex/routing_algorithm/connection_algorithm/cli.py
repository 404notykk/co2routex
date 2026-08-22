"""Command-line interface for candidate-connection generation."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import yaml

from .models import Settings
from .pipeline import run


def resolve_path(base: Path, value: object, key: str) -> Path:
    """Resolve a required path relative to the YAML file."""
    if value is None or not str(value).strip():
        raise ValueError(f"Configuration value '{key}' is required")

    path = Path(str(value)).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def read_string(
    config: dict[str, Any],
    key: str,
    default: str,
) -> str:
    value = config.get(key, default)

    if value is None or not str(value).strip():
        raise ValueError(f"Configuration value '{key}' cannot be empty")

    return str(value).strip()


def read_string_list(
    config: dict[str, Any],
    key: str,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    value = config.get(key, list(default))

    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(
            f"Configuration value '{key}' must be a YAML list"
        )

    cleaned = tuple(str(item).strip() for item in value)

    if not cleaned or any(not item for item in cleaned):
        raise ValueError(
            f"Configuration value '{key}' must contain non-empty strings"
        )

    return cleaned


def read_boolean(
    config: dict[str, Any],
    key: str,
    default: bool,
) -> bool:
    value = config.get(key, default)

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized in {"true", "yes", "on", "1"}:
            return True

        if normalized in {"false", "no", "off", "0"}:
            return False

    raise ValueError(f"Configuration value '{key}' must be true or false")


def read_integer(
    config: dict[str, Any],
    key: str,
    default: int,
) -> int:
    value = config.get(key, default)

    if isinstance(value, bool):
        raise ValueError(f"Configuration value '{key}' must be an integer")

    if isinstance(value, int):
        return value

    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as error:
            raise ValueError(
                f"Configuration value '{key}' must be an integer"
            ) from error

    raise ValueError(f"Configuration value '{key}' must be an integer")


def read_float(
    config: dict[str, Any],
    key: str,
    default: float,
) -> float:
    value = config.get(key, default)

    if isinstance(value, bool):
        raise ValueError(f"Configuration value '{key}' must be numeric")

    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Configuration value '{key}' must be numeric"
        ) from error


def read_optional_float(
    config: dict[str, Any],
    key: str,
) -> float | None:
    value = config.get(key)

    if value is None or (isinstance(value, str) and not value.strip()):
        return None

    if isinstance(value, bool):
        raise ValueError(
            f"Configuration value '{key}' must be numeric or null"
        )

    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Configuration value '{key}' must be numeric or null"
        ) from error


def load_settings(config_path: Path) -> Settings:
    """Load a YAML configuration into validated settings."""
    config_path = config_path.expanduser().resolve()

    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file does not exist: {config_path}"
        )

    if not config_path.is_file():
        raise ValueError(
            f"Configuration path is not a file: {config_path}"
        )

    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    if not isinstance(config, dict):
        raise ValueError("The YAML configuration must contain a mapping")

    base = config_path.parent
    workbook_path = resolve_path(
        base,
        config.get("workbook_path"),
        "workbook_path",
    )
    if not workbook_path.exists():
        raise FileNotFoundError(
            f"Input workbook does not exist: {workbook_path}"
        )

    return Settings(
        workbook_path=workbook_path,
        nodes_sheet=read_string(config, "nodes_sheet", "nodes"),
        mode_sheets=read_string_list(
            config,
            "mode_sheets",
            ("pipeline",),
        ),
        nodes_crs=read_string(config, "nodes_crs", "EPSG:4326"),
        method=read_string(config, "method", "all_eligible"),
        emitter_types=read_string_list(
            config,
            "emitter_types",
            ("cement", "refinery", "waste_to_energy"),
        ),
        storage_types=read_string_list(
            config,
            "storage_types",
            ("storage", "utilisation"),
        ),
        transport_type=read_string(
            config,
            "transport_type",
            "transport",
        ),
        allow_transport_to_transport=read_boolean(
            config,
            "allow_transport_to_transport",
            True,
        ),
        emitter_to_emitter_rule=read_string(
            config,
            "emitter_to_emitter_rule",
            "disabled",
        ),
        emitter_to_emitter_max_detour_factor=read_float(
            config,
            "emitter_to_emitter_max_detour_factor",
            1.5,
        ),
        k=read_integer(config, "k", 3),
        relative_distance_factor=read_float(
            config,
            "relative_distance_factor",
            1.5,
        ),
        max_distance_km=read_optional_float(
            config,
            "max_distance_km",
        ),
    )


def main() -> None:
    """Run the candidate-connection generator."""
    parser = argparse.ArgumentParser(
        description=(
            "Generate directed candidate connection matrices for the "
            "CO2 raster router"
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to the YAML configuration (default: config.yaml)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    settings = load_settings(args.config)
    logging.info("Connection-generation method: %s", settings.method)
    logging.info("Input workbook: %s", settings.workbook_path)
    result = run(settings)

    print(
        f"Generated {result.candidate_count} candidate connections "
        f"from {result.node_count} nodes. "
        f"Updated workbook: {result.workbook_path}"
    )


if __name__ == "__main__":
    main()
