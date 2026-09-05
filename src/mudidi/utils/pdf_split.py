"""Split individual pages from a PDF using PyMuPDF."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf


def parse_page_spec(spec: str) -> list[int]:
    """Expand a page specification into an ordered list of 1-based page numbers.

    Supports comma-separated singletons and hyphen-separated inclusive ranges,
    e.g. ``"97-123, 179-182"`` or ``"19, 83, 162"``. Returns ``[]`` for empty input.

    Raises:
        ValueError: When a token or range is malformed.
    """
    if not spec or not spec.strip():
        return []

    pages: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", chunk)
            if not match:
                raise ValueError(f"Unrecognised page range: {chunk!r}")
            start, end = int(match.group(1)), int(match.group(2))
            if start < 1 or end < 1:
                raise ValueError(f"Page numbers must be >= 1: {chunk!r}")
            if end < start:
                raise ValueError(f"Descending range not supported: {chunk!r}")
            pages.extend(range(start, end + 1))
        else:
            if not chunk.isdigit():
                raise ValueError(f"Unrecognised page token: {chunk!r}")
            page = int(chunk)
            if page < 1:
                raise ValueError(f"Page numbers must be >= 1: {chunk!r}")
            pages.append(page)
    return pages


def extract_pdf_pages(
    source_pdf: Path,
    page_numbers: list[int],
    output_dir: Path,
    *,
    stem_template: str = "page_{page}.pdf",
    overwrite: bool = False,
) -> list[Path]:
    """Extract ``page_numbers`` from ``source_pdf`` into ``output_dir``.

    Output files are named ``page_{N}.pdf`` by default (``N`` = source PDF page
    number). Returns paths in the same order as ``page_numbers``.

    The source PDF is opened once, regardless of how many pages are requested.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []
    total = len(page_numbers)
    print(
        f"PDF split: {source_pdf.name} ({total} requested page(s))",
        flush=True,
    )
    with pymupdf.open(str(source_pdf)) as source:
        for index, page in enumerate(page_numbers, start=1):
            if not 1 <= page <= source.page_count:
                raise ValueError(
                    f"page {page} is outside source PDF "
                    f"(1-{source.page_count})"
                )
            out_path = output_dir / stem_template.format(page=page)
            if out_path.exists() and not overwrite:
                action = "reused"
            else:
                with pymupdf.open() as destination:
                    destination.insert_pdf(
                        source,
                        from_page=page - 1,
                        to_page=page - 1,
                    )
                    destination.save(str(out_path))
                action = "wrote"
            print(
                f"PDF split: {action} source page {page} "
                f"({index}/{total})",
                flush=True,
            )
            results.append(out_path)
    print(
        f"PDF split complete: {source_pdf.name} ({len(results)} page(s))",
        flush=True,
    )
    return results
