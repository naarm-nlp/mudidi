from __future__ import annotations

from pathlib import Path

from mudidi.utils.dataset_statistics import build_dataset_statistics, write_dataset_statistics


def test_build_dataset_statistics_reports_page_and_dictionary_totals(
    tmp_path: Path,
) -> None:
    dictionaries = tmp_path / "dictionaries"
    dictionary = dictionaries / "Example-English"
    page_dir = dictionary / "Stage 1 Gold OCR" / "page_1"
    page_dir.mkdir(parents=True)
    (page_dir / "page_1_stage1_GOLD.tsv").write_text(
        "column_id\tline_number\ttext\n"
        "header\t\tExample dictionary\n"
        "left\t1\t<b>alpha</b> one\n"
        "left\t2\ttwo\n"
        "right\t1\tthree-four\n",
        encoding="utf-8",
    )
    mdf_dir = dictionary / "Stage 2 MDF file" / "page_1"
    mdf_dir.mkdir(parents=True)
    (mdf_dir / "page_1.mdf.txt").write_text(
        "\\lx alpha\n\\ge first\n\n\\lx beta\n\\ps noun\n",
        encoding="utf-8",
    )
    pdf_dir = dictionary / "Dictionary pages"
    pdf_dir.mkdir()
    (pdf_dir / "page_2.pdf").touch()

    statistics = build_dataset_statistics(dictionaries)

    assert statistics["summary"] == {
        "dictionary_count": 1,
        "page_count": 2,
        "pages_with_stage1_gold": 1,
        "pages_with_stage2_mdf": 1,
    }
    assert statistics["dictionaries"] == [
        {
            "dictionary": "Example-English",
            "page_count": 2,
            "pages_with_pdf": 1,
            "pages_with_stage1_gold": 1,
            "pages_with_stage2_mdf": 1,
            "rows": 3,
            "columns": 2,
            "tokens": 4,
            "tags": 4,
            "unique_tags": 3,
            "tag_counts": {"ge": 1, "lx": 2, "ps": 1},
        }
    ]
    assert statistics["pages"] == [
        {
            "dictionary": "Example-English",
            "page": "page_1",
            "has_pdf": False,
            "has_stage1_gold": True,
            "has_stage2_mdf": True,
            "rows": 3,
            "columns": 2,
            "tokens": 4,
            "tags": 4,
            "unique_tags": 3,
            "tag_counts": {"ge": 1, "lx": 2, "ps": 1},
        },
        {
            "dictionary": "Example-English",
            "page": "page_2",
            "has_pdf": True,
            "has_stage1_gold": False,
            "has_stage2_mdf": False,
            "rows": None,
            "columns": None,
            "tokens": None,
            "tags": None,
            "unique_tags": None,
            "tag_counts": {},
        },
    ]


def test_write_dataset_statistics_creates_json_and_csv_reports(tmp_path: Path) -> None:
    statistics = {
        "schema_version": 1,
        "definitions": {},
        "summary": {},
        "dictionaries": [],
        "pages": [],
    }

    paths = write_dataset_statistics(statistics, tmp_path)

    assert {path.name for path in paths} == {
        "dictionary_statistics.json",
        "dictionary_statistics_by_dictionary.csv",
        "dictionary_statistics_by_page.csv",
    }
    assert (tmp_path / "dictionary_statistics.json").is_file()
    assert (tmp_path / "dictionary_statistics_by_page.csv").read_text(encoding="utf-8").startswith(
        "dictionary,page,has_pdf"
    )
