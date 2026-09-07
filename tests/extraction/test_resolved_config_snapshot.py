from __future__ import annotations

import json
from pathlib import Path

from mudidi.cli.extract import _guides_manifest_entry, _write_run_config


def test_resolved_config_is_written_beside_stage_manifest(tmp_path: Path) -> None:
    stage_dir = tmp_path / "stage-1" / "experiment"

    _write_run_config(
        stage_dir,
        {"stage": "1"},
        force=False,
        resolved_config={"kind": "benchmark_run", "input": {"pages": "/pages"}},
    )

    assert json.loads((stage_dir / "run_config.json").read_text()) == {"stage": "1"}
    assert json.loads((stage_dir / "resolved_config.json").read_text())["kind"] == (
        "benchmark_run"
    )


def test_run_manifest_serializes_path_based_guides(tmp_path: Path) -> None:
    stage_dir = tmp_path / "stage-1" / "experiment"
    guides_path = tmp_path / "stage1.txt"

    _write_run_config(
        stage_dir,
        {
            "stage": "1",
            "stage1_guides": _guides_manifest_entry(guides_path, "Preserve accents."),
        },
        force=False,
    )

    manifest = json.loads((stage_dir / "run_config.json").read_text())
    assert manifest["stage1_guides"] == {
        "used": True,
        "path": str(guides_path),
        "text": "Preserve accents.",
    }


def test_resolved_config_resume_guard_preserves_existing_snapshot(tmp_path: Path) -> None:
    stage_dir = tmp_path / "stage-1" / "experiment"
    _write_run_config(
        stage_dir,
        {"stage": "1"},
        force=False,
        resolved_config={"version": 1},
    )

    _write_run_config(
        stage_dir,
        {"stage": "changed"},
        force=False,
        resolved_config={"version": 2},
    )

    assert json.loads((stage_dir / "run_config.json").read_text())["stage"] == "1"
    assert json.loads((stage_dir / "resolved_config.json").read_text())["version"] == 1
