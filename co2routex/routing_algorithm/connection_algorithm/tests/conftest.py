"""Shared fixtures for connection-algorithm tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from connection_algorithm.models import Settings


@pytest.fixture
def settings_factory(tmp_path: Path):
    def build(**overrides) -> Settings:
        values = {
            "workbook_path": tmp_path / "input.xlsx",
        }
        values.update(overrides)
        return Settings(**values)

    return build
