"""Tests for Stage 2 baseline comparison CSV generation."""

from __future__ import annotations

import csv
from pathlib import Path

from mudidi.cli.evaluate_stage2_mdf import _write_baseline_comparison_csv


def _write_summary(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_baseline_comparison_skips_aggregate_and_handles_extra_columns(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline.csv"
    new_summary = tmp_path / "new.csv"
    out = tmp_path / "comparison.csv"

    _write_summary(
        baseline,
        [
            {
                "experiment": "base_exp",
                "page_id": "Lang/page_1",
                "Entry_F1": "0.9",
                "MDF_Fields_F1": "0.8",
                "ReadOrderEdit": "0.1",
            },
            {
                "experiment": "base_exp",
                "page_id": "__aggregate__",
                "Entry_F1": "0.95",
                "MDF_Fields_F1": "0.85",
                "ReadOrderEdit": "0.05",
            },
        ],
    )
    _write_summary(
        new_summary,
        [
            {
                "experiment": "new_exp",
                "page_id": "Lang/page_1",
                "Entry_F1": "1.0",
                "MDF_Fields_F1": "0.9",
                "ReadOrderEdit": "0.0",
                "Field_Value_GCER": "0.01",
            },
            {
                "experiment": "new_exp",
                "page_id": "__aggregate__",
                "Entry_F1": "1.0",
                "MDF_Fields_F1": "0.9",
                "ReadOrderEdit": "0.0",
                "Field_Value_GCER": "0.01",
            },
        ],
    )

    _write_baseline_comparison_csv(
        new_summary=new_summary,
        baseline_summary=baseline,
        baseline_experiment="base_exp",
        output_path=out,
    )

    with out.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 1
    assert rows[0]["page_id"] == "Lang/page_1"
    assert rows[0]["delta_Entry_F1"] == "0.100000"
    assert rows[0]["delta_MDF_Fields_F1"] == "0.100000"
    assert rows[0]["baseline_Field_Value_GCER"] == ""
    assert rows[0]["Field_Value_GCER"] == "0.01"
    assert rows[0]["delta_Field_Value_GCER"] == ""


def test_baseline_comparison_tolerates_non_numeric_cells(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.csv"
    new_summary = tmp_path / "new.csv"
    out = tmp_path / "comparison.csv"

    _write_summary(
        baseline,
        [
            {
                "experiment": "base_exp",
                "page_id": "Lang/page_1",
                "Entry_F1": "n/a",
            },
        ],
    )
    _write_summary(
        new_summary,
        [
            {
                "experiment": "new_exp",
                "page_id": "Lang/page_1",
                "Entry_F1": "0.5",
            },
        ],
    )

    _write_baseline_comparison_csv(
        new_summary=new_summary,
        baseline_summary=baseline,
        baseline_experiment="base_exp",
        output_path=out,
    )

    with out.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["delta_Entry_F1"] == ""


def test_baseline_comparison_supports_language_page_schema(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.csv"
    current = tmp_path / "current.csv"
    output = tmp_path / "comparison.csv"
    _write_summary(
        baseline,
        [
            {
                "experiment": "base_exp",
                "language": "Lang",
                "page": "page_1",
                "Entry_F1": "0.8",
            },
            {
                "experiment": "base_exp",
                "language": "__aggregate__",
                "page": "__aggregate__",
                "Entry_F1": "0.9",
            },
        ],
    )
    _write_summary(
        current,
        [
            {
                "experiment": "new_exp",
                "language": "Lang",
                "page": "page_1",
                "Entry_F1": "1.0",
            },
            {
                "experiment": "new_exp",
                "language": "__aggregate__",
                "page": "__aggregate__",
                "Entry_F1": "1.0",
            },
        ],
    )

    _write_baseline_comparison_csv(
        new_summary=current,
        baseline_summary=baseline,
        baseline_experiment="base_exp",
        output_path=output,
    )

    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 1
    assert rows[0]["page_id"] == "Lang/page_1"
    assert rows[0]["delta_Entry_F1"] == "0.200000"
    assert "language" not in rows[0]
    assert "page" not in rows[0]
