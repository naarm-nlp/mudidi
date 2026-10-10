"""Tests for joining per-page Stage 2 MDF into one dictionary file."""

from __future__ import annotations

import json
from pathlib import Path

from mudidi.utils.combined_mdf import (
    COMBINED_MDF_FILENAME,
    COMBINED_MDF_REPORT_FILENAME,
    write_combined_mdf,
)


def _write_page(stage2_root: Path, stem: str, text: str) -> None:
    page_dir = stage2_root / stem
    page_dir.mkdir(parents=True)
    (page_dir / f"{stem}.mdf.txt").write_text(text, encoding="utf-8")


def test_continuation_page_joins_the_previous_pages_last_entry(tmp_path: Path) -> None:
    stage2 = tmp_path / "stage-2"
    _write_page(stage2, "page_10", "\\lx alo\n\\ge water\n")
    _write_page(stage2, "page_9", "\\lx ala\n\\ge fish\n")
    _write_page(stage2, "page_11", "\\se alo nu\n\\ge rain\n\n\\lx amu\n\\ge dog\n")

    report = write_combined_mdf(stage2, tmp_path)

    assert (tmp_path / COMBINED_MDF_FILENAME).read_text(encoding="utf-8") == (
        "\\lx ala\n\\ge fish\n\n"
        "\\lx alo\n\\ge water\n"
        "\\se alo nu\n\\ge rain\n\n"
        "\\lx amu\n\\ge dog\n"
    )
    assert report is not None
    assert report["pages"] == ["page_9", "page_10", "page_11"]
    assert report["pages_starting_mid_entry"] == [
        {
            "page": "page_11",
            "previous_page": "page_10",
            "first_line": "\\se alo nu",
            "joined_to_previous_page": True,
        }
    ]
    assert json.loads(
        (tmp_path / COMBINED_MDF_REPORT_FILENAME).read_text(encoding="utf-8")
    ) == report


def test_continuation_after_a_missing_page_is_not_attached(tmp_path: Path) -> None:
    stage2 = tmp_path / "stage-2"
    _write_page(stage2, "page_5", "\\ge orphan gloss\n")
    _write_page(stage2, "page_9", "\\xe left over\n\n\\lx b\n")

    report = write_combined_mdf(stage2, tmp_path)

    assert (tmp_path / COMBINED_MDF_FILENAME).read_text(encoding="utf-8") == (
        "\\ge orphan gloss\n\n\\xe left over\n\n\\lx b\n"
    )
    assert report is not None
    assert [
        (item["page"], item["previous_page"], item["joined_to_previous_page"])
        for item in report["pages_starting_mid_entry"]
    ] == [("page_5", None, False), ("page_9", "page_5", False)]


def test_longer_marker_starting_with_lx_is_not_a_headword(tmp_path: Path) -> None:
    stage2 = tmp_path / "stage-2"
    _write_page(stage2, "page_1", "\\lx a\n")
    _write_page(stage2, "page_2", "\\lxx odd\n")

    report = write_combined_mdf(stage2, tmp_path)

    assert report is not None
    assert [item["page"] for item in report["pages_starting_mid_entry"]] == ["page_2"]


def test_empty_pages_are_reported_and_skipped(tmp_path: Path) -> None:
    stage2 = tmp_path / "stage-2"
    _write_page(stage2, "page_1", "\\lx a\n")
    _write_page(stage2, "page_2", "\n")

    report = write_combined_mdf(stage2, tmp_path)

    assert report is not None
    assert report["pages"] == ["page_1"]
    assert report["empty_pages"] == ["page_2"]


def test_page_without_any_mdf_marker_is_left_out(tmp_path: Path) -> None:
    stage2 = tmp_path / "stage-2"
    _write_page(stage2, "page_1", "\\lx a\n")
    _write_page(stage2, "page_2", "I can't produce MDF for this page.\n")
    _write_page(stage2, "page_3", "\\ge left over\n")

    report = write_combined_mdf(stage2, tmp_path)

    assert (tmp_path / COMBINED_MDF_FILENAME).read_text(encoding="utf-8") == (
        "\\lx a\n\n\\ge left over\n"
    )
    assert report is not None
    assert report["pages"] == ["page_1", "page_3"]
    assert report["pages_without_mdf"] == ["page_2"]
    assert report["pages_starting_mid_entry"][0]["joined_to_previous_page"] is False


def test_nothing_is_written_without_page_mdf(tmp_path: Path) -> None:
    (tmp_path / "stage-2" / "page_1").mkdir(parents=True)

    assert write_combined_mdf(tmp_path / "stage-2", tmp_path) is None
    assert write_combined_mdf(tmp_path / "missing", tmp_path) is None
    assert not (tmp_path / COMBINED_MDF_FILENAME).exists()
