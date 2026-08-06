"""Build reproducible per-dictionary and per-language-script dataset statistics."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from mudidi.evaluation.stage1.per_language_quality import (
    gold_grapheme_counts_by_language_script,
)
from mudidi.evaluation.stage2.mdf_parser import parse_mdf
from mudidi.schemas.language_span import PageLanguageMap


_METADATA_COLUMNS = frozenset({"header", "footer"})
_PAGE_NUMBER_RE = re.compile(r"(\d+)$")
_LEGACY_REPORT_NAMES = (
    "dictionary_statistics_by_dictionary.csv",
    "dictionary_statistics_by_page.csv",
)


def _page_sort_key(page: str) -> tuple[str, int, str]:
    match = _PAGE_NUMBER_RE.search(page)
    return (
        page[: match.start()] if match else page,
        int(match.group(1)) if match else -1,
        page,
    )


def _body_rows(tsv_path: Path) -> list[dict[str, str]]:
    with tsv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required_columns = {"column_id", "line_number", "text"}
        if not reader.fieldnames or not required_columns <= set(reader.fieldnames):
            raise ValueError(f"Invalid Stage 1 TSV (missing required columns): {tsv_path}")
        return [
            dict(row)
            for row in reader
            if (column_id := (row.get("column_id") or "").strip())
            and column_id not in _METADATA_COLUMNS
        ]


def _required_stage1_artifact(path: Path, label: str) -> Path:
    if not path.is_file():
        raise ValueError(f"Stage 1 {label} missing: {path}")
    return path


def _stage1_metrics(tsv_path: Path) -> dict[str, Any]:
    rows = _body_rows(tsv_path)
    page_name = tsv_path.parent.name
    flat_path = _required_stage1_artifact(
        tsv_path.parent / f"{page_name}_stage1_GOLD_flat.txt",
        "flat file",
    )
    lang_map_path = _required_stage1_artifact(
        tsv_path.parent / f"{page_name}_lang.json",
        "language map",
    )
    raw_flat = flat_path.read_text(encoding="utf-8")
    try:
        language_script_graphemes = gold_grapheme_counts_by_language_script(
            raw_flat,
            PageLanguageMap.load(lang_map_path),
        )
    except ValueError as exc:
        raise ValueError(f"Invalid Stage 1 language map {lang_map_path}: {exc}") from exc
    return {
        "rows": len(rows),
        "columns": len({row["column_id"].strip() for row in rows}),
        "gold_grapheme_count": sum(language_script_graphemes.values()),
        "language_script_graphemes": language_script_graphemes,
        "bold_tag_count": raw_flat.count("<b>"),
        "italic_tag_count": raw_flat.count("<i>"),
    }


def _stage2_metrics(mdf_path: Path) -> dict[str, Any]:
    tag_counts = Counter(
        field.marker
        for record in parse_mdf(mdf_path.read_text(encoding="utf-8"))
        for field in record.lines
    )
    return {
        "tags": sum(tag_counts.values()),
        "unique_tags": len(tag_counts),
        "tag_counts": dict(sorted(tag_counts.items())),
    }


def _empty_page(dictionary: str, page: str) -> dict[str, Any]:
    return {
        "dictionary": dictionary,
        "page": page,
        "has_pdf": False,
        "has_stage1_gold": False,
        "has_stage2_mdf": False,
        "rows": None,
        "columns": None,
        "gold_grapheme_count": None,
        "language_script_graphemes": {},
        "bold_tag_count": None,
        "italic_tag_count": None,
        "tags": None,
        "unique_tags": None,
        "tag_counts": {},
    }


def _sum_available(rows: Iterable[dict[str, Any]], key: str) -> int | None:
    values = [row[key] for row in rows if row[key] is not None]
    return sum(values) if values else None


def _dictionary_statistics(dictionary: str, pages: list[dict[str, Any]]) -> dict[str, Any]:
    tag_counts: Counter[str] = Counter()
    for page in pages:
        tag_counts.update(page["tag_counts"])
    return {
        "dictionary": dictionary,
        "page_count": len(pages),
        "pages_with_pdf": sum(page["has_pdf"] for page in pages),
        "pages_with_stage1_gold": sum(page["has_stage1_gold"] for page in pages),
        "pages_with_stage2_mdf": sum(page["has_stage2_mdf"] for page in pages),
        "rows": _sum_available(pages, "rows"),
        "columns": _sum_available(pages, "columns"),
        "gold_grapheme_count": _sum_available(pages, "gold_grapheme_count"),
        "bold_tag_count": _sum_available(pages, "bold_tag_count"),
        "italic_tag_count": _sum_available(pages, "italic_tag_count"),
        "tags": _sum_available(pages, "tags"),
        "unique_tags": len(tag_counts) if tag_counts else None,
        "tag_counts": dict(sorted(tag_counts.items())),
    }


def build_dataset_statistics(dictionaries_dir: Path) -> dict[str, Any]:
    """Collect page, grapheme, markup, and MDF-tag dataset statistics."""
    if not dictionaries_dir.is_dir():
        raise ValueError(f"Dictionary dataset directory not found: {dictionaries_dir}")

    page_statistics: list[dict[str, Any]] = []
    dictionary_statistics: list[dict[str, Any]] = []

    dictionary_dirs = sorted(
        path
        for path in dictionaries_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    for dictionary_dir in dictionary_dirs:
        pages: dict[str, dict[str, Any]] = {}

        for pdf_path in sorted((dictionary_dir / "Dictionary pages").glob("page_*.pdf")):
            page = pages.setdefault(
                pdf_path.stem,
                _empty_page(dictionary_dir.name, pdf_path.stem),
            )
            page["has_pdf"] = True

        for tsv_path in sorted(
            (dictionary_dir / "Stage 1 Gold OCR").glob("*/*_stage1_GOLD.tsv")
        ):
            page_name = tsv_path.parent.name
            page = pages.setdefault(
                page_name,
                _empty_page(dictionary_dir.name, page_name),
            )
            page["has_stage1_gold"] = True
            page.update(_stage1_metrics(tsv_path))

        for mdf_path in sorted((dictionary_dir / "Stage 2 MDF file").glob("*/*.mdf.txt")):
            page_name = mdf_path.parent.name
            page = pages.setdefault(
                page_name,
                _empty_page(dictionary_dir.name, page_name),
            )
            page["has_stage2_mdf"] = True
            page.update(_stage2_metrics(mdf_path))

        dictionary_pages = sorted(
            pages.values(),
            key=lambda page: _page_sort_key(page["page"]),
        )
        page_statistics.extend(dictionary_pages)
        dictionary_statistics.append(
            _dictionary_statistics(dictionary_dir.name, dictionary_pages)
        )

    return {
        "schema_version": 2,
        "definitions": {
            "page": "A page identifier discovered from a PDF, Stage 1 TSV, or Stage 2 MDF artifact.",
            "rows": "Number of Stage 1 TSV body rows; header and footer rows are excluded.",
            "columns": "Number of distinct non-metadata Stage 1 TSV column_id values, summed across pages for dictionary totals.",
            "gold_grapheme_count": "Stage 1 gold graphemes after evaluation normalization, with Unicode punctuation, whitespace, meta, and space labels excluded.",
            "language_script_graphemes": "Gold grapheme counts grouped by validated Stage 1 language-script label.",
            "bold_tag_count": "Exact opening <b> tag occurrences in raw Stage 1 gold flat text.",
            "italic_tag_count": "Exact opening <i> tag occurrences in raw Stage 1 gold flat text.",
            "tags": "Total Stage 2 MDF field-marker occurrences, summed across pages for dictionary totals.",
            "unique_tags": "Distinct Stage 2 MDF field markers; dictionary totals are distinct across all of its pages.",
            "null_metric": "The source artifact for that metric is unavailable for the page or dictionary.",
        },
        "summary": {
            "dictionary_count": len(dictionary_statistics),
            "page_count": len(page_statistics),
            "pages_with_stage1_gold": sum(
                page["has_stage1_gold"] for page in page_statistics
            ),
            "pages_with_stage2_mdf": sum(
                page["has_stage2_mdf"] for page in page_statistics
            ),
        },
        "dictionaries": dictionary_statistics,
        "pages": page_statistics,
    }


def _detailed_csv_rows(statistics: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "language": page["dictionary"],
            "page": page["page"],
            "language_script": language_script,
            "gold_grapheme_count": count,
        }
        for page in statistics["pages"]
        for language_script, count in sorted(
            page["language_script_graphemes"].items()
        )
    ]


def _summary_csv_rows(statistics: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "language": row["dictionary"],
            **{
                key: value
                for key, value in row.items()
                if key not in {"dictionary", "tag_counts"}
            },
            "tag_counts": json.dumps(
                row["tag_counts"],
                ensure_ascii=False,
                sort_keys=True,
            ),
        }
        for row in statistics["dictionaries"]
    ]


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_dataset_statistics(statistics: dict[str, Any], output_dir: Path) -> list[Path]:
    """Write JSON plus detailed and summary CSV reports, returning their paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "dictionary_statistics.json"
    detailed_path = (
        output_dir / "dictionary_statistics_per_language_script_detailed.csv"
    )
    summary_path = output_dir / "dictionary_statistics_summary.csv"

    json_path.write_text(
        json.dumps(statistics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        detailed_path,
        _detailed_csv_rows(statistics),
        ["language", "page", "language_script", "gold_grapheme_count"],
    )
    _write_csv(
        summary_path,
        _summary_csv_rows(statistics),
        [
            "language",
            "page_count",
            "pages_with_pdf",
            "pages_with_stage1_gold",
            "pages_with_stage2_mdf",
            "rows",
            "columns",
            "gold_grapheme_count",
            "bold_tag_count",
            "italic_tag_count",
            "tags",
            "unique_tags",
            "tag_counts",
        ],
    )

    for report_name in _LEGACY_REPORT_NAMES:
        (output_dir / report_name).unlink(missing_ok=True)

    return [json_path, detailed_path, summary_path]
