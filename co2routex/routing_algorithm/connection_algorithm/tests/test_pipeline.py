"""End-to-end workbook generation test."""

from pathlib import Path

import pandas as pd

from connection_algorithm.pipeline import run


def test_pipeline_updates_input_workbook_in_place(
    tmp_path: Path,
    settings_factory,
):
    workbook_path = tmp_path / "input.xlsx"
    nodes = pd.DataFrame(
        [
            {
                "node_id": "E",
                "node_name": "Emitter",
                "longitude": 5.0,
                "latitude": 52.0,
                "node_type": "cement",
                "annual_flux": 1.0,
            },
            {
                "node_id": "S",
                "node_name": "Storage",
                "longitude": 5.5,
                "latitude": 52.0,
                "node_type": "storage",
                "annual_flux": None,
            },
        ]
    )

    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        nodes.to_excel(writer, sheet_name="nodes", index=False)
        pd.DataFrame({"obsolete": [999]}).to_excel(
            writer,
            sheet_name="pipeline",
            index=False,
        )
        pd.DataFrame({"keep_me": ["unchanged"]}).to_excel(
            writer,
            sheet_name="metadata",
            index=False,
        )

    settings = settings_factory(workbook_path=workbook_path)
    result = run(settings)

    assert result.node_count == 2
    assert result.candidate_count == 1
    assert result.workbook_path == workbook_path
    assert result.workbook_path.exists()

    matrix = pd.read_excel(
        result.workbook_path,
        sheet_name="pipeline",
        index_col=0,
    )
    matrix.index = matrix.index.map(str)
    matrix.columns = matrix.columns.map(str)

    assert int(matrix.loc["E", "S"]) == 1
    assert int(matrix.loc["S", "E"]) == 0
    assert int(matrix.loc["E", "E"]) == 0

    metadata = pd.read_excel(result.workbook_path, sheet_name="metadata")
    assert metadata.loc[0, "keep_me"] == "unchanged"

    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "generated_connections.csv").exists()
