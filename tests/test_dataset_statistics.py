from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from mudidi.evaluation.stage1.per_language_quality import (
    gold_grapheme_counts_by_language_script,
)
from mudidi.schemas.language_span import LanguageSpan, PageLanguageMap, sha256_of
from mudidi.utils.dataset_statistics import build_dataset_statistics, write_dataset_statistics


def _write_dataset_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    dictionaries = tmp_path / "dictionaries"
    dictionary = dictionaries / "Example-English"
    page_dir = dictionary / "Stage 1 Gold OCR" / "page_1"
    page_dir.mkdir(parents=True)

    english_line = "<b>alpha</b> e\u0301!"
    chinese_line = "<i>\u4f60</i> \u559c\u6b22\uff0c"
    raw_flat = f"META\n{english_line}\n{chinese_line}"
    flat_path = page_dir / "page_1_stage1_GOLD_flat.txt"
    flat_path.write_text(raw_flat, encoding="utf-8")
    (page_dir / "page_1_stage1_GOLD.tsv").write_text(
        "column_id\tline_number\ttext\n"
        "header\t\tMETA\n"
        f"left\t1\t{english_line}\n"
        f"right\t1\t{chinese_line}\n",
        encoding="utf-8",
    )

    meta_end = len("META\n")
    english_end = meta_end + len(english_line) + 1
    lang_map_path = page_dir / "page_1_lang.json"
    PageLanguageMap(
        dictionary="Example-English",
        page=1,
        source_text_sha=sha256_of(raw_flat),
        labeled_via="label-studio",
        spans=[
            LanguageSpan(start=0, end=meta_end, language="meta"),
            LanguageSpan(start=meta_end, end=english_end, language="English-Latin"),
            LanguageSpan(start=english_end, end=len(raw_flat), language="Chinese-Han"),
        ],
    ).save(lang_map_path)

    mdf_dir = dictionary / "Stage 2 MDF file" / "page_1"
    mdf_dir.mkdir(parents=True)
    (mdf_dir / "page_1.mdf.txt").write_text(
        "\\lx alpha\n\\ge first\n\n\\lx beta\n\\ps noun\n",
        encoding="utf-8",
    )
    pdf_dir = dictionary / "Dictionary pages"
    pdf_dir.mkdir()
    (pdf_dir / "page_2.pdf").touch()
    return dictionaries, flat_path, lang_map_path


def test_gold_grapheme_counts_match_evaluation_language_projection(tmp_path: Path) -> None:
    _dictionaries, flat_path, lang_map_path = _write_dataset_fixture(tmp_path)

    counts = gold_grapheme_counts_by_language_script(
        flat_path.read_text(encoding="utf-8"),
        PageLanguageMap.load(lang_map_path),
    )

    assert counts == {"Chinese-Han": 3, "English-Latin": 6}


def test_build_dataset_statistics_reports_page_and_dictionary_totals(
    tmp_path: Path,
) -> None:
    dictionaries, _flat_path, _lang_map_path = _write_dataset_fixture(tmp_path)

    statistics = build_dataset_statistics(dictionaries)

    assert statistics["schema_version"] == 3
    assert statistics["summary"] == {
        "dictionary_count": 1,
        "stage1_page_count": 1,
        "stage2_page_count": 1,
    }
    assert statistics["dictionaries"] == [
        {
            "dictionary": "Example-English",
            "stage1_page_count": 1,
            "stage2_page_count": 1,
            "rows": 2,
            "columns": 2,
            "gold_grapheme_count": 9,
            "bold_tag_count": 1,
            "italic_tag_count": 1,
            "tags": 4,
            "unique_tags": 3,
            "tag_counts": {"ge": 1, "lx": 2, "ps": 1},
        }
    ]
    assert statistics["pages"] == [
        {
            "dictionary": "Example-English",
            "page": "page_1",
            "has_stage1_gold": True,
            "has_stage2_mdf": True,
            "rows": 2,
            "columns": 2,
            "gold_grapheme_count": 9,
            "language_script_graphemes": {
                "Chinese-Han": 3,
                "English-Latin": 6,
            },
            "bold_tag_count": 1,
            "italic_tag_count": 1,
            "tags": 4,
            "unique_tags": 3,
            "tag_counts": {"ge": 1, "lx": 2, "ps": 1},
        },
    ]
    assert "characters" not in statistics["definitions"]


def test_write_dataset_statistics_creates_detailed_and_summary_csvs(
    tmp_path: Path,
) -> None:
    dictionaries, _flat_path, _lang_map_path = _write_dataset_fixture(tmp_path)
    statistics = build_dataset_statistics(dictionaries)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    legacy_page = output_dir / "dictionary_statistics_by_page.csv"
    legacy_dictionary = output_dir / "dictionary_statistics_by_dictionary.csv"
    legacy_page.touch()
    legacy_dictionary.touch()

    paths = write_dataset_statistics(statistics, output_dir)

    assert {path.name for path in paths} == {
        "dictionary_statistics.json",
        "dictionary_statistics_per_language_script_detailed.csv",
        "dictionary_statistics_summary.csv",
    }
    assert not legacy_page.exists()
    assert not legacy_dictionary.exists()

    with (output_dir / "dictionary_statistics_per_language_script_detailed.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        detailed_reader = csv.DictReader(handle)
        detailed_fields = detailed_reader.fieldnames
        detailed_rows = list(detailed_reader)
    assert detailed_fields == [
        "language",
        "page",
        "language_script",
        "gold_grapheme_count",
    ]
    assert detailed_rows == [
        {
            "language": "Example-English",
            "page": "page_1",
            "language_script": "Chinese-Han",
            "gold_grapheme_count": "3",
        },
        {
            "language": "Example-English",
            "page": "page_1",
            "language_script": "English-Latin",
            "gold_grapheme_count": "6",
        },
    ]

    with (output_dir / "dictionary_statistics_summary.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        summary_reader = csv.DictReader(handle)
        summary_fields = summary_reader.fieldnames or []
        summary_rows = list(summary_reader)
    assert "page" not in summary_fields
    assert "language_script" not in summary_fields
    assert summary_fields == [
        "language",
        "stage1_page_count",
        "stage2_page_count",
        "rows",
        "columns",
        "gold_grapheme_count",
        "bold_tag_count",
        "italic_tag_count",
        "tags",
        "unique_tags",
        "tag_counts",
    ]
    assert summary_rows[0]["language"] == "Example-English"
    assert summary_rows[0]["gold_grapheme_count"] == "9"
    assert summary_rows[0]["bold_tag_count"] == "1"
    assert summary_rows[0]["italic_tag_count"] == "1"


def test_statistics_cli_reports_both_stage_page_counts(
    tmp_path: Path,
) -> None:
    dictionaries, _flat_path, _lang_map_path = _write_dataset_fixture(tmp_path)
    output_dir = tmp_path / "output"

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts/generate_dataset_statistics.py"),
            "--dictionaries-dir",
            str(dictionaries),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(
        "Wrote statistics for 1 dictionaries, 1 Stage 1 pages, and 1 Stage 2 pages:"
    )


def test_flat_only_stage1_page_keeps_text_metrics_without_rows_or_columns(
    tmp_path: Path,
) -> None:
    dictionaries, flat_path, _lang_map_path = _write_dataset_fixture(tmp_path)
    flat_path.with_name("page_1_stage1_GOLD.tsv").unlink()

    statistics = build_dataset_statistics(dictionaries)

    assert statistics["summary"]["stage1_page_count"] == 1
    assert statistics["dictionaries"][0]["rows"] is None
    assert statistics["dictionaries"][0]["columns"] is None
    assert statistics["dictionaries"][0]["gold_grapheme_count"] == 9
    assert statistics["pages"][0]["rows"] is None
    assert statistics["pages"][0]["columns"] is None
    assert statistics["pages"][0]["gold_grapheme_count"] == 9


def test_orphan_stage1_tsv_is_not_counted_as_a_stage1_page(tmp_path: Path) -> None:
    dictionaries, flat_path, _lang_map_path = _write_dataset_fixture(tmp_path)
    flat_path.unlink()

    statistics = build_dataset_statistics(dictionaries)

    assert statistics["summary"]["stage1_page_count"] == 0
    assert statistics["dictionaries"][0]["stage1_page_count"] == 0


def test_stage1_statistics_require_language_map(tmp_path: Path) -> None:
    dictionaries, _flat_path, lang_map_path = _write_dataset_fixture(tmp_path)
    lang_map_path.unlink()

    with pytest.raises(ValueError, match="language map"):
        build_dataset_statistics(dictionaries)


def test_stage1_statistics_reject_stale_language_map(tmp_path: Path) -> None:
    dictionaries, flat_path, lang_map_path = _write_dataset_fixture(tmp_path)
    flat_path.write_text(flat_path.read_text(encoding="utf-8") + " stale", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        build_dataset_statistics(dictionaries)

    assert str(exc_info.value) == (
        f"Invalid Stage 1 language map {lang_map_path}: "
        "source_text_sha does not match the provided gold text"
    )
