from __future__ import annotations

import json
from pathlib import Path

from mudidi.cli.extract import _write_run_config


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
