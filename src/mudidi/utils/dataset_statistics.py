"""Build reproducible per-dictionary and per-page dataset statistics."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import grapheme

from mudidi.evaluation.stage2.mdf_parser import parse_mdf
from mudidi.evaluation.stage1.tag_parser import strip_tags


_METADATA_COLUMNS = frozenset({"header", "footer"})
_PAGE_NUMBER_RE = re.compile(r"(\d+)$")


def _page_sort_key(page: str) -> tuple[str, int, str]:
    match = _PAGE_NUMBER_RE.search(page)
    return (page[: match.start()] if match else page, int(match.group(1)) if match else -1, page)


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


def _stage1_metrics(tsv_path: Path) -> dict[str, int]:
    rows = _body_rows(tsv_path)
    return {
        "rows": len(rows),
        "columns": len({row["column_id"].strip() for row in rows}),
        "characters": sum(
            sum(
                not cluster.isspace()
                for cluster in grapheme.graphemes(strip_tags(row.get("text") or ""))
            )
            for row in rows
        ),
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
        "characters": None,
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
        "characters": _sum_available(pages, "characters"),
        "tags": _sum_available(pages, "tags"),
        "unique_tags": len(tag_counts) if tag_counts else None,
        "tag_counts": dict(sorted(tag_counts.items())),
    }


def build_dataset_statistics(dictionaries_dir: Path) -> dict[str, Any]:
    """Collect layout, character, and MDF-tag counts from a dictionary dataset."""
    if not dictionaries_dir.is_dir():
        raise ValueError(f"Dictionary dataset directory not found: {dictionaries_dir}")

    page_statistics: list[dict[str, Any]] = []
    dictionary_statistics: list[dict[str, Any]] = []

    for dictionary_dir in sorted(
        path for path in dictionaries_dir.iterdir() if path.is_dir() and not path.name.startswith(".")
    ):
        pages: dict[str, dict[str, Any]] = {}

        for pdf_path in sorted((dictionary_dir / "Dictionary pages").glob("page_*.pdf")):
            page = pages.setdefault(pdf_path.stem, _empty_page(dictionary_dir.name, pdf_path.stem))
            page["has_pdf"] = True

        for tsv_path in sorted(
            (dictionary_dir / "Stage 1 Gold OCR").glob("*/*_stage1_GOLD.tsv")
        ):
            page_name = tsv_path.parent.name
            page = pages.setdefault(page_name, _empty_page(dictionary_dir.name, page_name))
            page["has_stage1_gold"] = True
            page.update(_stage1_metrics(tsv_path))

        for mdf_path in sorted((dictionary_dir / "Stage 2 MDF file").glob("*/*.mdf.txt")):
            page_name = mdf_path.parent.name
            page = pages.setdefault(page_name, _empty_page(dictionary_dir.name, page_name))
            page["has_stage2_mdf"] = True
            page.update(_stage2_metrics(mdf_path))

        dictionary_pages = sorted(pages.values(), key=lambda page: _page_sort_key(page["page"]))
        page_statistics.extend(dictionary_pages)
        dictionary_statistics.append(_dictionary_statistics(dictionary_dir.name, dictionary_pages))

    return {
        "schema_version": 1,
        "definitions": {
            "page": "A page identifier discovered from a PDF, Stage 1 TSV, or Stage 2 MDF artifact.",
            "rows": "Number of Stage 1 TSV body rows; header and footer rows are excluded.",
            "columns": "Number of distinct non-metadata Stage 1 TSV column_id values, summed across pages for dictionary totals.",
            "characters": "Total non-whitespace Unicode grapheme clusters in Stage 1 body text after inline HTML markup removal.",
            "tags": "Total Stage 2 MDF field-marker occurrences, summed across pages for dictionary totals.",
            "unique_tags": "Distinct Stage 2 MDF field markers; dictionary totals are distinct across all of its pages.",
            "null_metric": "The source artifact for that metric is unavailable for the page or dictionary.",
        },
        "summary": {
            "dictionary_count": len(dictionary_statistics),
            "page_count": len(page_statistics),
            "pages_with_stage1_gold": sum(page["has_stage1_gold"] for page in page_statistics),
            "pages_with_stage2_mdf": sum(page["has_stage2_mdf"] for page in page_statistics),
        },
        "dictionaries": dictionary_statistics,
        "pages": page_statistics,
    }


def _csv_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "tag_counts": json.dumps(row["tag_counts"], ensure_ascii=False, sort_keys=True),
        }
        for row in rows
    ]


def write_dataset_statistics(statistics: dict[str, Any], output_dir: Path) -> list[Path]:
    """Write JSON plus CSV views of the statistics, returning their paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "dictionary_statistics.json"
    dictionaries_path = output_dir / "dictionary_statistics_by_dictionary.csv"
    pages_path = output_dir / "dictionary_statistics_by_page.csv"

    json_path.write_text(
        json.dumps(statistics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    reports = (
        (
            dictionaries_path,
            _csv_rows(statistics["dictionaries"]),
            [
                "dictionary",
                "page_count",
                "pages_with_pdf",
                "pages_with_stage1_gold",
                "pages_with_stage2_mdf",
                "rows",
                "columns",
                "characters",
                "tags",
                "unique_tags",
                "tag_counts",
            ],
        ),
        (
            pages_path,
            _csv_rows(statistics["pages"]),
            [
                "dictionary",
                "page",
                "has_pdf",
                "has_stage1_gold",
                "has_stage2_mdf",
                "rows",
                "columns",
                "characters",
                "tags",
                "unique_tags",
                "tag_counts",
            ],
        ),
    )
    for path, rows, fieldnames in reports:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    return [json_path, dictionaries_path, pages_path]
