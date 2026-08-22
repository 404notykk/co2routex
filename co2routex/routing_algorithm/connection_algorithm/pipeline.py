"""Orchestrate one candidate-connection generation run."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .generator import generate_candidates
from .inputs import load_nodes
from .models import Settings
from .outputs import write_connection_matrices


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunResult:
    """Summary of one completed generation run."""

    node_count: int
    candidate_count: int
    workbook_path: Path
    elapsed_seconds: float


def run(settings: Settings) -> RunResult:
    """Load nodes, generate candidates, and update the workbook."""
    started = time.perf_counter()
    nodes = load_nodes(settings.workbook_path, settings.nodes_sheet)
    candidates = generate_candidates(nodes, settings)

    write_connection_matrices(
        workbook_path=settings.workbook_path,
        nodes_sheet=settings.nodes_sheet,
        mode_sheets=settings.mode_sheets,
        node_ids=list(nodes),
        candidates=candidates,
    )

    elapsed = time.perf_counter() - started

    LOGGER.info(
        "Generated %s candidate connections from %s nodes in %.2f seconds",
        len(candidates),
        len(nodes),
        elapsed,
    )
    LOGGER.info("Updated workbook in place: %s", settings.workbook_path)

    return RunResult(
        node_count=len(nodes),
        candidate_count=len(candidates),
        workbook_path=settings.workbook_path,
        elapsed_seconds=elapsed,
    )
