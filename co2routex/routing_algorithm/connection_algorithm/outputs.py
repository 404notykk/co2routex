"""Write generated connection matrices into the input workbook."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pandas as pd

from .models import CandidateConnection


def build_connection_matrix(
    node_ids: list[str],
    candidates: list[CandidateConnection],
) -> pd.DataFrame:
    """Build a square directed 0/1 connection matrix."""
    matrix = pd.DataFrame(
        0,
        index=node_ids,
        columns=node_ids,
        dtype=int,
    )
    matrix.index.name = "node_id"

    for candidate in candidates:
        matrix.loc[candidate.from_id, candidate.to_id] = 1

    return matrix


def write_connection_matrices(
    workbook_path: Path,
    nodes_sheet: str,
    mode_sheets: tuple[str, ...],
    node_ids: list[str],
    candidates: list[CandidateConnection],
) -> None:
    """Atomically replace configured mode sheets in the input workbook.

    A temporary copy is edited and validated first. The original workbook
    is replaced only after the complete update succeeds. No persistent
    output workbook or candidate CSV is created.
    """
    workbook_path = workbook_path.resolve()
    workbook_path.parent.mkdir(parents=True, exist_ok=True)
    matrix = build_connection_matrix(node_ids, candidates)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{workbook_path.stem}_connections_",
        suffix=".xlsx",
        dir=workbook_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)

    try:
        shutil.copy2(workbook_path, temporary_path)

        with pd.ExcelWriter(
            temporary_path,
            engine="openpyxl",
            mode="a",
            if_sheet_exists="replace",
        ) as writer:
            for mode in mode_sheets:
                matrix.to_excel(writer, sheet_name=mode)

        with pd.ExcelFile(temporary_path) as workbook:
            expected_sheets = {nodes_sheet, *mode_sheets}
            missing_sheets = expected_sheets - set(workbook.sheet_names)

            if missing_sheets:
                raise RuntimeError(
                    "Updated workbook is missing expected worksheets: "
                    f"{sorted(missing_sheets)}"
                )

        os.replace(temporary_path, workbook_path)
    finally:
        temporary_path.unlink(missing_ok=True)
