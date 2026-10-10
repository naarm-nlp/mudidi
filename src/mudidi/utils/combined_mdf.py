"""Join per-page Stage 2 MDF into one dictionary file.

Inference parses each page locally, so an entry that crosses a page break is
split between two page files: the later page opens with fields that have no
``\\lx`` above them. Appending the pages in order re-attaches those fields to
the entry they belong to.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mudidi.utils.stage2_page_selection import page_sort_key

COMBINED_MDF_FILENAME = "dictionary.mdf.txt"
COMBINED_MDF_REPORT_FILENAME = "dictionary.mdf.report.json"

_HEADWORD_LINE = re.compile(r"^\\lx(\s|$)")
_MARKER_LINE = re.compile(r"^\\[A-Za-z]", re.MULTILINE)


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _is_adjacent(previous_stem: str, stem: str) -> bool:
    """Whether ``stem`` is the page printed directly after ``previous_stem``."""
    prev_numbered, prev_page, prev_sub, _ = page_sort_key(previous_stem)
    numbered, page, sub, _ = page_sort_key(stem)
    if prev_numbered != 0 or numbered != 0:
        return False
    if page == prev_page:
        return sub == prev_sub + 1
    return page == prev_page + 1 and sub <= 1


def write_combined_mdf(stage2_root: Path, output_dir: Path) -> dict[str, Any] | None:
    """Write the combined dictionary MDF and its join report under ``output_dir``.

    A page whose file holds no MDF marker at all (for example a model's
    refusal) is left out and listed under ``pages_without_mdf``.

    A page that opens mid-entry is appended directly to the previous page so
    its fields join that page's last entry. Every such page is listed in the
    report for spot-checking; when the previous page is missing from the run
    the fields cannot be attached and stay a separate block.

    Returns the report, or ``None`` when no page MDF exists yet.
    """
    if not stage2_root.is_dir():
        return None
    page_files = sorted(
        (
            page_dir / f"{page_dir.name}.mdf.txt"
            for page_dir in stage2_root.iterdir()
            if page_dir.is_dir() and (page_dir / f"{page_dir.name}.mdf.txt").is_file()
        ),
        key=lambda path: page_sort_key(path.parent.name),
    )
    if not page_files:
        return None

    combined = ""
    pages: list[str] = []
    empty_pages: list[str] = []
    pages_without_mdf: list[str] = []
    mid_entry: list[dict[str, Any]] = []
    previous_stem: str | None = None
    for page_file in page_files:
        stem = page_file.parent.name
        text = page_file.read_text(encoding="utf-8").strip()
        if not text:
            empty_pages.append(stem)
            continue
        if not _MARKER_LINE.search(text):
            pages_without_mdf.append(stem)
            continue
        pages.append(stem)
        first_line = _first_line(text)
        attached = False
        if not _HEADWORD_LINE.match(first_line):
            attached = previous_stem is not None and _is_adjacent(previous_stem, stem)
            mid_entry.append(
                {
                    "page": stem,
                    "previous_page": previous_stem,
                    "first_line": first_line,
                    "joined_to_previous_page": attached,
                }
            )
        if combined:
            combined += "\n" if attached else "\n\n"
        combined += text
        previous_stem = stem

    report: dict[str, Any] = {
        "output": COMBINED_MDF_FILENAME,
        "pages": pages,
        "empty_pages": empty_pages,
        "pages_without_mdf": pages_without_mdf,
        "pages_starting_mid_entry": mid_entry,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / COMBINED_MDF_FILENAME).write_text(combined + "\n", encoding="utf-8")
    (output_dir / COMBINED_MDF_REPORT_FILENAME).write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report
